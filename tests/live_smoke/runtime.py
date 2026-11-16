"""Opt-in isolated app runtime. Importing this module performs no I/O."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from copy import deepcopy
from functools import partial
import asyncio
import ipaddress
import json
import os
import socket
import sys
from unittest.mock import patch
from uuid import uuid4

from tests.live_smoke.guard import GENERATION_MODEL, EMBEDDING_MODEL, GuardedTransport, GuardStop

QUESTION = "What is the remote work allowance?"
BODY = {"query": QUESTION, "retrieval_limit": 5, "final_limit": 3, "lambda_value": 0.5, "use_cache": True}


def configured():
    from dotenv import load_dotenv
    load_dotenv()
    from src import config
    if (os.getenv("GENERATION_MODEL", GENERATION_MODEL) != GENERATION_MODEL or
            config.EMBEDDING_MODEL != EMBEDDING_MODEL or config.QDRANT_URL != "http://localhost:6333" or
            config.QDRANT_COLLECTION != "enterprise_documents" or config.REDIS_CACHE_URL != "redis://localhost:6381/2" or
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") != "https://api.openai.com/v1" or
            not os.getenv("OPENAI_API_KEY") or not os.getenv("JWT_SECRET_KEY")):
        raise GuardStop()
    if any(name in sys.modules for name in ("src.ingestion.embedder", "src.generation.generator", "src.rag.pipeline")):
        raise GuardStop()  # Never instrument a previously initialized application process.


@contextmanager
def network_policy():
    gate = ContextVar("smoke_provider_send", default=False)
    ips = set()
    dns, connect, connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def host_text(host):
        # AnyIO hands socket.getaddrinfo() ASCII bytes, including for loopback.
        # Normalize only the representation, never widen the destination allowlist.
        if isinstance(host, bytes):
            try:
                host = host.decode("ascii")
            except UnicodeDecodeError:
                raise GuardStop() from None
        if not isinstance(host, str):
            raise GuardStop()
        return host

    def local(host):
        host = host_text(host)
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host == "localhost"

    def guarded_dns(host, *args, **kwargs):
        normalized = host_text(host)
        if not local(normalized) and not (gate.get() and normalized == "api.openai.com"):
            raise GuardStop()
        result = dns(host, *args, **kwargs)
        if normalized == "api.openai.com":
            ips.update(item[4][0] for item in result)
        return result

    async def contextual_dns(loop, host, port, *, family=0, type=0, proto=0, flags=0):
        # BaseEventLoop.getaddrinfo otherwise loses the task-local send gate in
        # run_in_executor. Copy only this DNS call's context, not a global permission.
        resolve = partial(socket.getaddrinfo, host, port, family=family, type=type, proto=proto, flags=flags)
        return await loop.run_in_executor(None, copy_context().run, resolve)

    def allowed(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            host, port = address[:2]
            host = host_text(host)
            if not local(host) and not (gate.get() and host in ips and port == 443):
                raise GuardStop()

    def guarded_connect(sock, address):
        allowed(sock, address)
        return connect(sock, address)

    def guarded_ex(sock, address):
        allowed(sock, address)
        return connect_ex(sock, address)

    @contextmanager
    def sending():
        token = gate.set(True)
        try:
            yield
        finally:
            gate.reset(token)

    with patch.object(asyncio.BaseEventLoop, "getaddrinfo", contextual_dns), patch.object(socket, "getaddrinfo", guarded_dns), patch.object(socket.socket, "connect", guarded_connect), patch.object(socket.socket, "connect_ex", guarded_ex):
        yield sending


def sdk_client(ledger, transport):
    import openai
    from openai import DefaultAsyncHttpxClient
    return openai.AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"], base_url="https://api.openai.com/v1",
        max_retries=0, timeout=30.0,
        http_client=DefaultAsyncHttpxClient(transport=transport, trust_env=False, follow_redirects=False),
    )


class ScopedCache:
    def __init__(self, ledger):
        self.ledger = ledger
        self.prefix = "live-smoke:" + uuid4().hex + ":"
        self.keys = set()

    def key(self, original):
        def build(*args, **kwargs):
            key = self.prefix + original(*args, **kwargs)
            self.keys.add(key)
            return key
        return build

    def lookup(self, original):
        def get(**kwargs):
            if self.ledger.phase == 3:
                if kwargs.get("tenant_id") != "acme" or self.ledger.snapshot is None:
                    self.ledger.stop()
                return deepcopy(self.ledger.snapshot)
            result = original(**kwargs)
            if result is not None:
                self.ledger.stop()  # First two must establish a fresh live baseline.
            return result
        return get

    def pin(self, redis, key, first):
        if not key.startswith(self.prefix) or redis.ttl(key) < 30:
            self.ledger.stop()
        try:
            value = json.loads(redis.get(key))
            validate_answer(value, "acme")
        except Exception:
            self.ledger.stop()
        if value != first:
            self.ledger.stop()
        self.ledger.snapshot = deepcopy(value)

    def cleanup(self, redis):
        if self.keys:
            redis.delete(*sorted(self.keys))  # Only exact run-owned keys, never existing entries.


def validate_answer(result, tenant):
    from src.api.schemas import QueryResponse
    result = QueryResponse.model_validate(result).model_dump()
    expected = "500 USD" if tenant == "acme" else "2000 USD"
    forbidden = "2000 USD" if tenant == "acme" else "500 USD"
    if expected not in result["answer"] or forbidden in result["answer"] or not result["citations"]:
        raise GuardStop()
    primary = False
    for citation in result["citations"]:
        if f'[{citation["citation_id"]}]' not in result["answer"]:
            raise GuardStop()
        for source in citation["sources"]:
            allowed = {("remote-work-policy", f"{tenant}-policy.pdf"),
                       ("employee-handbook", "employee-handbook.pdf" if tenant == "acme" else "globex-handbook.pdf")}
            if tenant == "acme":
                allowed.add(("second-document", "employee-handbook.pdf"))
            if (source["document_id"], source["filename"]) not in allowed:
                raise GuardStop()
            primary |= source["document_id"] == "remote-work-policy"
    if not primary:
        raise GuardStop()
    return result


class ReadOnlyQdrant:
    """Expose only the retrieval operations the existing query path needs."""
    def __init__(self, client, ledger):
        self._client, self._ledger = client, ledger

    def __getattr__(self, name):
        if name not in {"collection_exists", "scroll", "query_points", "close"}:
            self._ledger.stop()
        return getattr(self._client, name)


class ScopedRedis:
    """Never permit a command or key outside the smoke cache namespace."""
    def __init__(self, client, scope):
        self.client, self.scope = client, scope

    def checked(self, key):
        if key not in self.scope.keys or not key.startswith(self.scope.prefix):
            self.scope.ledger.stop()

    def get(self, key):
        self.checked(key)
        return self.client.get(key)

    def set(self, key, value, **kwargs):
        self.checked(key)
        return self.client.set(key, value, **kwargs)

    def ttl(self, key):
        self.checked(key)
        return self.client.ttl(key)

    def delete(self, *keys):
        for key in keys:
            self.checked(key)
        return self.client.delete(*keys)
