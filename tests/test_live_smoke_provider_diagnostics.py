"""Real async DNS/SDK control flow, fake resolver/provider/socket, no external I/O."""
import asyncio
import base64
from decimal import Decimal
import json
import os
import socket
import ssl
import struct
import unittest
from unittest.mock import patch

import anyio
import httpx2 as httpx

from tests.live_smoke.guard import GuardedTransport, GuardStop, Ledger
from tests.live_smoke.runtime import network_policy, sdk_client

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.9", 443))]
BODY = {"model": "text-embedding-3-small", "input": "synthetic", "encoding_format": "float"}


class DNSProvider(httpx.AsyncBaseTransport):
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        if self.mode == "dns":
            raise socket.gaierror("SENSITIVE_EXCEPTION_TEXT")
        if self.mode == "tls":
            try:
                raise ssl.SSLCertVerificationError("SENSITIVE_EXCEPTION_TEXT")
            except ssl.SSLError as exc:
                raise httpx.ConnectError("SENSITIVE_EXCEPTION_TEXT") from exc
        if self.mode == "timeout":
            raise httpx.ReadTimeout("SENSITIVE_EXCEPTION_TEXT")
        await anyio.getaddrinfo("api.openai.com", 443, type=socket.SOCK_STREAM)
        if type(self.mode) is int:
            return httpx.Response(self.mode, json={"error": {"message": "SENSITIVE_BODY_API_KEY"}})
        body = json.loads(request.content)
        vector = [0.25] * 1536
        if body.get("encoding_format") == "base64":
            vector = base64.b64encode(struct.pack("<1536f", *vector)).decode("ascii")
        value = {"object": "list", "model": "text-embedding-3-small",
                 "data": [{"object": "embedding", "index": 0, "embedding": vector}],
                 "usage": {"prompt_tokens": 10, "total_tokens": 10}}
        if self.mode == "missing_usage":
            value.pop("usage")
        if self.mode == "invalid_json":
            return httpx.Response(200, content=b"SENSITIVE_INVALID_JSON")
        return httpx.Response(200, json=value)


class ProviderDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def make(self, provider, sending):
        ledger = Ledger(); ledger.begin(1, "acme")
        return ledger, GuardedTransport(ledger, provider, sending)

    async def send(self, transport):
        return await transport.handle_async_request(httpx.Request(
            "POST", "https://api.openai.com/v1/embeddings", json=BODY))

    async def test_original_executor_context_loss_reproduces_exact_ledger(self):
        original = asyncio.BaseEventLoop.getaddrinfo
        with patch.object(socket, "getaddrinfo", return_value=PUBLIC) as dns, \
             patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
            with network_policy() as sending:
                ledger, transport = self.make(DNSProvider(), sending)
                # Restore only the previous async DNS bridge to reproduce the old bug.
                with patch.object(asyncio.BaseEventLoop, "getaddrinfo", original):
                    with self.assertRaises(GuardStop):
                        await self.send(transport)
            dns.assert_not_called()
        self.assertEqual(ledger.counts, {"embedding": 1, "generation": 0, "count": 0})
        self.assertEqual(ledger.reserved, Decimal("0.00016384"))
        self.assertEqual(ledger.actual, 0)
        self.assertEqual(ledger.failure["exception_class"], "GuardStop")
        self.assertEqual(ledger.failure["failure_stage"], "transport")
        self.assertEqual(ledger.failure["category"], "network_guard")
        self.assertIn(":guarded_dns:", ledger.failure["source_location"])
        self.assertIsNone(ledger.failure["http_status"])

    async def test_fixed_executor_propagates_gate_with_real_sdk_default_serialization(self):
        original = asyncio.BaseEventLoop.getaddrinfo
        with patch.object(socket, "getaddrinfo", return_value=PUBLIC) as dns, \
             patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
            with network_policy() as sending:
                provider = DNSProvider()
                ledger, transport = self.make(provider, sending)
                with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only-not-a-key"}):
                    client = sdk_client(ledger, transport)
                response = await client.embeddings.create(model="text-embedding-3-small", input="synthetic")
                self.assertEqual(len(response.data[0].embedding), 1536)
                await client.close()
            self.assertIs(asyncio.BaseEventLoop.getaddrinfo, original)
            self.assertEqual(dns.call_count, 1)
        self.assertFalse(ledger.aborted)
        self.assertIsNone(ledger.failure)
        self.assertEqual(ledger.events[0]["input_actual"], 10)
        self.assertEqual(ledger.reserved, Decimal("0.00016384"))
        self.assertEqual(provider.calls, 1)

    async def test_concurrent_unguarded_task_never_inherits_send_gate(self):
        with patch.object(socket, "getaddrinfo", return_value=PUBLIC) as dns:
            with network_policy() as sending:
                async def guarded():
                    with sending():
                        return await anyio.getaddrinfo("api.openai.com", 443)
                async def unguarded():
                    with self.assertRaises(GuardStop):
                        await anyio.getaddrinfo("api.openai.com", 443)
                await asyncio.gather(guarded(), unguarded())
                with self.assertRaises(GuardStop):
                    await anyio.getaddrinfo("api.openai.com", 443)
            self.assertEqual(dns.call_count, 1)

    async def test_sanitized_transport_http_and_validation_categories(self):
        modes = [("dns", "transport", "dns_failure", None),
                 ("tls", "transport", "tls_failure", None),
                 ("timeout", "transport", "transport_timeout", None),
                 (401, "provider_http", "provider_authentication", 401),
                 (403, "provider_http", "provider_permission", 403),
                 (429, "provider_http", "provider_rate_limit_or_quota", 429),
                 (500, "provider_http", "provider_server_error", 500),
                 ("missing_usage", "response_validation", "guard_validation", 200),
                 ("invalid_json", "response_decode", "response_decode_failure", 200)]
        for mode, stage, category, status in modes:
            with self.subTest(category=category), patch.object(socket, "getaddrinfo", return_value=PUBLIC):
                with network_policy() as sending:
                    provider = DNSProvider(mode)
                    ledger, transport = self.make(provider, sending)
                    with self.assertRaises(GuardStop):
                        await self.send(transport)
                    first = dict(ledger.failure)
                    with self.assertRaises(GuardStop):
                        await self.send(transport)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(ledger.failure, first)
                self.assertEqual(first["failure_stage"], stage)
                self.assertEqual(first["category"], category)
                self.assertEqual(first["http_status"], status)
                self.assertEqual(ledger.reserved, Decimal("0.00016384"))
                self.assertEqual(set(first), {"exception_class", "failure_stage", "http_status", "category", "source_location"})
                self.assertNotIn("SENSITIVE", json.dumps(ledger.summary()))

    async def test_guarded_scope_still_cannot_resolve_other_public_hosts(self):
        with patch.object(socket, "getaddrinfo", return_value=PUBLIC) as dns:
            with network_policy() as sending:
                with sending():
                    for host in ("example.com", "huggingface.co", "8.8.8.8", "api.openai.com.evil"):
                        with self.assertRaises(GuardStop):
                            await anyio.getaddrinfo(host, 443)
            dns.assert_not_called()

    async def test_count_http_status_does_not_leak_into_generation_transport_failure(self):
        class CountThenFail(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if request.url.path.endswith("input_tokens"):
                    return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
                raise httpx.ConnectError("SENSITIVE_EXCEPTION_TEXT")
        ledger, transport = self.make(CountThenFail(), None)
        body = {"model": "gpt-4.1-mini", "input": "synthetic", "instructions": "synthetic",
                "text": {"format": {"type": "json_schema", "schema": {}, "name": "test", "strict": True}}}
        with self.assertRaises(GuardStop):
            await transport.handle_async_request(httpx.Request("POST", "https://api.openai.com/v1/responses", json=body))
        self.assertIsNone(ledger.failure["http_status"])
        self.assertEqual(ledger.failure["category"], "transport_failure")
        self.assertGreater(ledger.reserved, 0)
