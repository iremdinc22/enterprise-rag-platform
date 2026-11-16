"""All tests use in-memory transports/fake stores; no local services or provider I/O."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import os
import socket
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import httpx2 as httpx
from tests.live_smoke.guard import (Ledger, GuardedTransport, GuardStop, BUDGET,
                                   WORST_RESERVATION, MAX_INPUT)
from tests.live_smoke.runtime import ScopedCache, network_policy, sdk_client

GEN = {"model": "gpt-4.1-mini", "instructions": "system", "input": "question",
       "text": {"format": {"type": "json_schema", "name": "test", "schema": {}, "strict": True}}}
EMB = {"model": "text-embedding-3-small", "input": "question", "encoding_format": "float"}
ANSWER = {"answer": "Allowance is 500 USD [1].", "citations": [{"citation_id": 1, "sources": [
    {"document_id": "remote-work-policy", "filename": "acme-policy.pdf", "page": 1, "chunk_id": "chunk-001"}]}]}


class FakeProvider(httpx.AsyncBaseTransport):
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = []
        self.mode = "ok"
        self.tokens = 100

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        path = request.url.path
        # At the actual outgoing boundary: every request has already been counted/reserved.
        kind = "count" if path.endswith("input_tokens") else "embedding" if path.endswith("embeddings") else "generation"
        assert self.ledger.counts[kind] > 0
        assert self.ledger.events[-1]["kind"] == kind
        self.calls.append((path, body))
        if kind != "count" and self.mode == "timeout":
            raise httpx.ReadTimeout("secret-payload", request=request)
        if kind != "count" and self.mode == "status":
            return httpx.Response(500, json={"error": "secret-payload"})
        if kind == "count":
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": self.tokens})
        assert self.ledger.reserved > 0
        if kind == "embedding":
            items = [body["input"]] if isinstance(body["input"], str) else body["input"]
            data = {"model": "text-embedding-3-small", "data": [
                {"object": "embedding", "index": i, "embedding": [0.1] * 1536} for i, _ in enumerate(items)],
                "usage": {"prompt_tokens": 10, "total_tokens": 10}}
        else:
            data = {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed", "service_tier": "default",
                    "model": "gpt-4.1-mini-2025-04-14", "output": [], "parallel_tool_calls": False,
                    "tool_choice": "auto", "tools": [], "usage": {"input_tokens": self.tokens,
                    "output_tokens": 25, "total_tokens": self.tokens + 25}, "error": None}
        if self.mode == "citation_retry" and kind == "generation":
            citation = 999 if self.ledger.counts["generation"] == 1 else 1
            data["output"] = [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                               "content": [{"type": "output_text", "annotations": [],
                                            "text": json.dumps({"answer": f"Allowance is 500 USD [{citation}].",
                                                                "answered": True, "citation_ids": [citation]})}]}]
        if self.mode == "missing_usage":
            data.pop("usage")
        if self.mode == "over_output" and kind == "generation":
            data["usage"]["output_tokens"] = 501
        if self.mode == "count_mismatch" and kind == "generation":
            data["usage"]["input_tokens"] += 1
        return httpx.Response(200, json=data)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.ledger.begin(1, "acme")
        self.provider = FakeProvider(self.ledger)
        self.transport = GuardedTransport(self.ledger, self.provider)

    async def call(self, body=GEN, path="responses"):
        return await self.transport.handle_async_request(httpx.Request(
            "POST", "https://api.openai.com/v1/" + path, json=deepcopy(body)))

    async def test_output_cap_injected_at_serialized_boundary(self):
        await self.call({**GEN, "max_output_tokens": 99999})
        self.assertEqual(self.provider.calls[-1][1]["max_output_tokens"], 500)
        self.assertFalse(self.provider.calls[-1][1]["store"])
        self.assertEqual(self.provider.calls[-1][1]["service_tier"], "default")

    async def test_complete_schema_is_counted_before_paid_request(self):
        await self.call()
        counted = self.provider.calls[0][1]
        paid = self.provider.calls[1][1]
        for key in ("input", "instructions", "model", "text", "truncation"):
            self.assertEqual(counted[key], paid[key])
        self.assertEqual(self.ledger.events[-1]["input_bound"], 100)

    async def test_reservation_precedes_every_forwarded_call(self):
        await self.call(EMB, "embeddings")
        await self.call()
        self.assertEqual([e["kind"] for e in self.ledger.events], ["embedding", "count", "generation"])

    async def test_two_citation_attempts_are_independently_reserved(self):
        await self.call()
        first = self.ledger.reserved
        await self.call({**GEN, "input": "retry with validation feedback"})
        self.assertEqual(self.ledger.reserved, first * 2)
        before = len(self.provider.calls)
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertEqual(len(self.provider.calls), before)

    async def test_insufficient_budget_blocks_paid_call(self):
        self.ledger.budget = Decimal("0.00001")
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertEqual(len(self.provider.calls), 1)  # Only zero-cost counting, no generation.
        self.assertEqual(self.ledger.counts["generation"], 0)

    async def test_ambiguous_call_keeps_reservation_and_aborts(self):
        self.provider.mode = "timeout"
        with self.assertRaises(GuardStop) as exc:
            await self.call()
        self.assertNotIn("secret", str(exc.exception))
        held = self.ledger.reserved
        self.assertGreater(held, 0)
        self.assertTrue(self.ledger.aborted)
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertEqual(self.ledger.reserved, held)
        self.assertEqual(len(self.provider.calls), 2)

    async def test_server_error_does_not_retry_or_refund(self):
        self.provider.mode = "status"
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertEqual(len(self.provider.calls), 2)
        self.assertGreater(self.ledger.reserved, 0)

    async def test_unknown_usage_stops_the_run(self):
        self.provider.mode = "missing_usage"
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertTrue(self.ledger.aborted)
        self.assertGreater(self.ledger.reserved, 0)

    async def test_count_over_limit_prevents_generation(self):
        self.provider.tokens = MAX_INPUT + 1
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertEqual(len(self.provider.calls), 1)

    async def test_usage_mismatch_fails_closed(self):
        self.provider.mode = "count_mismatch"
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertTrue(self.ledger.aborted)

    async def test_provider_ignoring_output_cap_fails_closed(self):
        self.provider.mode = "over_output"
        with self.assertRaises(GuardStop):
            await self.call()
        self.assertTrue(self.ledger.aborted)

    async def test_embedding_shapes_and_calls_are_bounded(self):
        await self.call(EMB, "embeddings")
        await self.call({**EMB, "input": ["text"] * 5}, "embeddings")
        self.assertEqual(self.ledger.reserved, Decimal(6 * 8192) * Decimal(".02") / 1000000)
        with self.assertRaises(GuardStop):
            await self.call(EMB, "embeddings")
        self.assertEqual(len(self.provider.calls), 2)

    async def test_embedding_unknown_usage_retains_bound(self):
        self.provider.mode = "missing_usage"
        with self.assertRaises(GuardStop):
            await self.call(EMB, "embeddings")
        self.assertEqual(self.ledger.reserved, Decimal(8192) * Decimal(".02") / 1000000)

    async def test_unknown_paths_tools_models_streaming_blocked(self):
        cases = [({**GEN, "model": "different"}, "responses"),
                 ({**GEN, "tools": [{"type": "web_search"}]}, "responses"),
                 ({**GEN, "stream": True}, "responses"), (GEN, "files")]
        for body, path in cases:
            self.setUp()
            with self.assertRaises(GuardStop):
                await self.call(body, path)
            self.assertFalse(self.provider.calls)

    async def test_third_request_blocks_count_and_paid_paths(self):
        self.ledger.finish()
        self.ledger.begin(2, "globex")
        self.ledger.finish()
        self.ledger.snapshot = deepcopy(ANSWER)
        self.ledger.begin(3, "acme")
        for path in ("responses", "embeddings", "responses/input_tokens"):
            with self.assertRaises(GuardStop):
                await self.call(EMB if path == "embeddings" else GEN, path)
        self.assertFalse(self.provider.calls)

    async def test_full_run_maximum_reservation_and_counts(self):
        self.provider.tokens = MAX_INPUT
        for index, tenant in ((1, "acme"), (2, "globex")):
            if index == 2:
                self.ledger.begin(index, tenant)
            await self.call(EMB, "embeddings")
            await self.call({**EMB, "input": ["text"] * 5}, "embeddings")
            await self.call()
            await self.call()
            self.ledger.finish()
        self.assertEqual(self.ledger.reserved, WORST_RESERVATION)
        self.assertLess(WORST_RESERVATION, BUDGET)
        self.assertEqual(self.ledger.counts, {"embedding": 4, "generation": 4, "count": 4})

    async def test_real_generator_citation_retry_stays_inside_guard(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "network-free-unit-test"}):
            from src.generation import generator
            client = sdk_client(self.ledger, self.transport)
        self.provider.mode = "citation_retry"
        with patch.object(generator, "client", client):
            result = await generator.generate_answer(
                "Allowance?", "[SOURCE 1]\n500 USD", "gpt-4.1-mini",
                citations=[{"citation_id": 1, "sources": [{"document_id": "policy"}]}],
            )
        self.assertEqual(result.citation_ids, [1])
        self.assertEqual(self.ledger.counts["generation"], 2)
        self.assertEqual(self.ledger.counts["count"], 2)
        self.assertGreater(self.ledger.reserved, 0)
        self.assertTrue(all(body["max_output_tokens"] == 500 for path, body in self.provider.calls if path.endswith("responses")))
        await client.close()

    async def test_sdk_retry_settings_and_real_sdk_serialization(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "network-free-unit-test"}):
            client = sdk_client(self.ledger, self.transport)
        self.assertEqual(client.max_retries, 0)
        self.assertFalse(client._client.follow_redirects)
        await client.embeddings.create(model="text-embedding-3-small", input="question", encoding_format="float")
        self.assertEqual(self.ledger.counts["embedding"], 1)
        from pydantic import BaseModel
        class Output(BaseModel):
            answer: str
        await client.responses.parse(model="gpt-4.1-mini", input="question", instructions="system", text_format=Output)
        self.assertEqual(self.provider.calls[-1][1]["max_output_tokens"], 500)
        await client.close()


class CacheAndRuntimeTests(unittest.TestCase):
    def test_request_order_and_missing_baseline(self):
        ledger = Ledger()
        with self.assertRaises(GuardStop):
            ledger.begin(3, "acme")
        ledger = Ledger()
        ledger.begin(1, "acme"); ledger.finish()
        ledger.begin(2, "globex"); ledger.finish()
        with self.assertRaises(GuardStop):
            ledger.begin(3, "acme")

    def test_third_hit_is_pinned_even_if_redis_disappears(self):
        ledger = Ledger()
        cache = ScopedCache(ledger)
        fake = SimpleNamespace(ttl=lambda key: 100, get=lambda key: json.dumps(ANSWER))
        cache.pin(fake, cache.prefix + "key", ANSWER)
        for n, tenant in ((1, "acme"), (2, "globex")):
            ledger.begin(n, tenant); ledger.finish()
        ledger.begin(3, "acme")
        def forbidden(**kwargs):
            self.fail("Third lookup must not touch Redis/fallback")
        result = cache.lookup(forbidden)(tenant_id="acme")
        self.assertEqual(result, ANSWER)
        result["answer"] = "modified"
        self.assertEqual(ledger.snapshot, ANSWER)

    def test_expired_mismatched_or_cross_tenant_cache_rejected(self):
        for ttl, value in ((0, ANSWER), (100, {**ANSWER, "answer": "different"}), (100, {})):
            ledger = Ledger(); cache = ScopedCache(ledger)
            fake = SimpleNamespace(ttl=lambda key: ttl, get=lambda key: json.dumps(value))
            with self.assertRaises(GuardStop):
                cache.pin(fake, cache.prefix + "key", ANSWER)

    def test_existing_redis_keys_untouched(self):
        ledger = Ledger(); cache = ScopedCache(ledger)
        store = {"existing-key": "must-remain"}
        def delete(*keys):
            for key in keys:
                self.assertTrue(key.startswith(cache.prefix))
                store.pop(key, None)
        wrapped = cache.key(lambda tenant: "rag-result:" + tenant)
        store[wrapped("acme")] = "temporary"
        cache.cleanup(SimpleNamespace(delete=delete))
        self.assertEqual(store, {"existing-key": "must-remain"})

    def test_existing_cache_hit_in_first_query_is_unexpected(self):
        ledger = Ledger(); ledger.begin(1, "acme")
        cache = ScopedCache(ledger)
        with self.assertRaises(GuardStop):
            cache.lookup(lambda **kwargs: ANSWER)(tenant_id="acme")

    def test_unguarded_provider_connection_is_blocked_without_dns(self):
        with network_policy():
            with self.assertRaises(GuardStop):
                socket.getaddrinfo("api.openai.com", 443)
            with socket.socket() as sock:
                with self.assertRaises(GuardStop):
                    sock.connect(("8.8.8.8", 443))


    def test_qdrant_writes_are_not_exposed(self):
        from tests.live_smoke.runtime import ReadOnlyQdrant
        ledger = Ledger()
        writes = []
        backend = SimpleNamespace(scroll=lambda: "read", upsert=lambda: writes.append("write"))
        client = ReadOnlyQdrant(backend, ledger)
        self.assertEqual(client.scroll(), "read")
        for method in ("upsert", "delete", "create_collection", "delete_collection"):
            with self.assertRaises(GuardStop):
                getattr(client, method)
        self.assertFalse(writes)

    def test_redis_existing_keys_cannot_be_read_written_or_deleted(self):
        from tests.live_smoke.runtime import ScopedRedis
        ledger = Ledger(); cache = ScopedCache(ledger)
        actions = []
        backend = SimpleNamespace(get=lambda key: actions.append(key), set=lambda *a, **k: actions.append(a),
                                  delete=lambda *a: actions.append(a), ttl=lambda key: 100)
        scoped = ScopedRedis(backend, cache)
        for method, args in (("get", ("existing",)), ("set", ("existing", "change")), ("delete", ("existing",))):
            with self.assertRaises(GuardStop):
                getattr(scoped, method)(*args)
        self.assertFalse(actions)

    def test_default_cli_never_executes(self):
        from tests.live_smoke import run
        with patch("sys.argv", ["smoke"]), patch.object(run, "execute") as execute:
            self.assertEqual(run.main(), 0)
            execute.assert_not_called()
