"""DNS representation regressions; all sockets/provider transports are fake."""
import asyncio
import ipaddress
import os
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import anyio
from fastapi.testclient import TestClient

from tests.live_smoke.guard import GuardStop, Ledger, GuardedTransport
from tests.live_smoke.runtime import ReadOnlyQdrant, network_policy

LOOPBACK = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 6333))]


class NetworkPolicyRegressionTests(unittest.TestCase):
    def test_anyio_byte_hostname_reaches_only_fake_loopback_dns(self):
        # No real DNS/socket call: verify AnyIO's actual normalization + corrected policy.
        with patch.object(socket, "getaddrinfo", return_value=LOOPBACK) as dns:
            with network_policy():
                result = asyncio.run(anyio.getaddrinfo("localhost", 6333, type=socket.SOCK_STREAM))
        self.assertEqual(result[0][4], ("127.0.0.1", 6333))
        self.assertEqual(dns.call_args.args[0], b"localhost")

    def test_text_and_byte_loopback_representations_are_equivalent(self):
        for host in ("localhost", b"localhost", "127.0.0.1", b"127.0.0.1", "::1", b"::1"):
            with self.subTest(kind=type(host).__name__), patch.object(socket, "getaddrinfo", return_value=LOOPBACK) as dns:
                with network_policy():
                    socket.getaddrinfo(host, 6333)
                dns.assert_called_once()

    def test_public_byte_hosts_and_ips_still_never_reach_dns(self):
        with patch.object(socket, "getaddrinfo", return_value=[]) as dns:
            with network_policy():
                for host in ("api.openai.com", b"api.openai.com", b"huggingface.co", b"8.8.8.8", b"localhost.evil", b"\xff", None):
                    with self.assertRaises(GuardStop):
                        socket.getaddrinfo(host, 443)
            dns.assert_not_called()

    def test_authorized_provider_scope_normalizes_bytes_but_is_not_reused(self):
        # Fake all networking: this checks the gate, not the provider.
        public = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.9", 443))]
        with patch.object(socket, "getaddrinfo", return_value=public) as dns, \
             patch.object(socket.socket, "connect", return_value=None) as connect:
            with network_policy() as sending:
                with sending():
                    socket.getaddrinfo(b"api.openai.com", 443)
                    with socket.socket() as sock:
                        sock.connect(("203.0.113.9", 443))
                        with self.assertRaises(GuardStop):
                            sock.connect(("203.0.113.9", 80))
                with socket.socket() as sock:
                    with self.assertRaises(GuardStop):
                        sock.connect(("203.0.113.9", 443))
                with self.assertRaises(GuardStop):
                    socket.getaddrinfo(b"api.openai.com", 443)
            self.assertEqual(dns.call_count, 1)
            self.assertEqual(connect.call_count, 1)

    def test_bm25_read_path_failure_location_and_zero_provider_budget(self):
        # Exercise the installed real async Qdrant SDK through a fake DNS failure.
        from qdrant_client import AsyncQdrantClient
        client = AsyncQdrantClient(url="http://localhost:6333", check_compatibility=False)
        ledger = Ledger(); ledger.begin(1, "acme")
        provider = AsyncMock()
        guarded_provider = GuardedTransport(ledger, provider)
        async def read():
            try:
                with patch.object(socket, "getaddrinfo", side_effect=GuardStop()) as dns:
                    with self.assertRaises(Exception) as raised:
                        await ReadOnlyQdrant(client, ledger).scroll(
                            collection_name="enterprise_documents", limit=1, with_payload=False, with_vectors=False)
                self.assertEqual(type(raised.exception).__name__, "ResponseHandlingException")
                self.assertIsInstance(raised.exception.__cause__ or raised.exception.__context__, GuardStop)
                self.assertTrue(dns.called)
            finally:
                await client.close()
                await guarded_provider.aclose()
        asyncio.run(read())
        provider.handle_async_request.assert_not_called()
        self.assertEqual(ledger.counts, {"embedding": 0, "generation": 0, "count": 0})
        self.assertEqual(ledger.reserved, 0)

    def test_http_500_before_fix_and_successful_read_after_fix_without_provider(self):
        # Same read-only dependency and HTTP error conversion as the production route.
        # It intentionally ends at the retrieval boundary and never invokes a provider.
        async def scroll(**kwargs):
            await anyio.getaddrinfo("localhost", 6333, type=socket.SOCK_STREAM)
            return ([], None)
        ledger = Ledger(); ledger.begin(1, "acme")
        backend = SimpleNamespace(scroll=scroll)
        client = ReadOnlyQdrant(backend, ledger)
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "network-test-only-secret-000000000000000000"}):
            from src.api.main import create_app
            from src.api.dependencies import get_pipeline
            from src.auth.jwt_handler import create_access_token
        app = create_app()
        async def read_only_pipeline(**kwargs):
            await client.scroll(collection_name="enterprise_documents")
            return {"answer": "read completed", "citations": []}
        app.dependency_overrides[get_pipeline] = lambda: read_only_pipeline
        headers = {"Authorization": "Bearer " + create_access_token("unit-user", "acme", "employee")}

        # Pre-fix rule from the diagnosed implementation: bytes fail both checks.
        def old_policy(host, *args, **kwargs):
            try:
                allowed = ipaddress.ip_address(host).is_loopback
            except ValueError:
                allowed = host == "localhost"
            if not allowed:
                raise GuardStop()
            return LOOPBACK
        http = TestClient(app)  # No lifespan: no model/client startup in this network-free regression.
        try:
            with patch.object(socket, "getaddrinfo", side_effect=old_policy):
                failed = http.post("/api/v1/query", json={"query": "synthetic test"}, headers=headers)
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(failed.json(), {"detail": "Query processing failed"})
            with patch.object(socket, "getaddrinfo", return_value=LOOPBACK) as dns:
                with network_policy():
                    succeeded = http.post("/api/v1/query", json={"query": "synthetic test"}, headers=headers)
            self.assertEqual(succeeded.status_code, 200)
            self.assertTrue(dns.called)
        finally:
            http.close()
        self.assertEqual(ledger.counts, {"embedding": 0, "generation": 0, "count": 0})
        self.assertEqual(ledger.reserved, 0)
