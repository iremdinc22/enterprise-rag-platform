"""Deadlines and provider limits: fake HTTP transports only, no service access."""
import asyncio
import json
import os
import time
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENAI_API_KEY", "deadline-tests-not-a-key")
os.environ.setdefault("JWT_SECRET_KEY", "deadline-tests-only-000000000000000000000")

import httpx
import httpx2
import openai
from src.api.main import create_app
from src.api.dependencies import authenticated_user, get_pipeline
from src.api.resource_limits import QueryBodyLimit, QueryDeadline, QueryLimits
from src.auth.jwt_handler import create_access_token
from src.generation import generator
from src.ingestion import embedder
from src.rag import execution

RESULT = {"answer": "Synthetic [1]", "citations": [{"citation_id": 1, "sources": [
    {"document_id": "policy", "filename": "acme-policy.pdf", "page": 1, "chunk_id": "chunk-001"}]}]}
GROUPS = [{"citation_id": 1, "sources": RESULT["citations"][0]["sources"]}]


def generated(ids):
    return SimpleNamespace(usage=None, output_parsed=generator.GeneratedAnswer(
        answer="Synthetic " + " ".join(f"[{i}]" for i in ids), answered=True, citation_ids=ids))


class ProviderLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_timeout_retry_settings_and_one_transport_attempt(self):
        self.assertEqual(generator.client.max_retries, 0)
        self.assertEqual(embedder.query_client.max_retries, 0)
        self.assertEqual(generator.client.timeout.connect, 5)
        self.assertEqual(generator.client.timeout.read, 20)
        self.assertEqual(embedder.query_client.timeout.connect, 5)
        self.assertEqual(embedder.query_client.timeout.read, 20)
        self.assertEqual(embedder.client.max_retries, 1)  # Existing ingestion client unchanged.
        requests = []
        async def fail(request):
            requests.append(request)
            raise httpx2.ReadTimeout("PRIVATE", request=request)
        async with openai.AsyncOpenAI(api_key="test-only", max_retries=0,
                http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(fail))) as client:
            with self.assertRaises(execution.ProviderTimeout):
                await execution.provider_request(client.embeddings.create, model="text-embedding-3-small", input="Synthetic")
        self.assertEqual(len(requests), 1)
        self.assertLessEqual(requests[0].extensions["timeout"]["connect"], 5)
        self.assertLessEqual(requests[0].extensions["timeout"]["read"], 20)

    async def test_real_sdk_generation_serializes_output_cap_without_retry(self):
        bodies = []
        async def fail(request):
            bodies.append(json.loads(await request.aread()))
            return httpx2.Response(503, json={"error": {"message": "PRIVATE", "type": "server_error"}})
        async with openai.AsyncOpenAI(api_key="test-only", max_retries=0,
                http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(fail))) as client:
            with patch.object(generator, "client", client), self.assertRaises(execution.ProviderUnavailable):
                await generator.generate_answer("Policy?", "[SOURCE 1]\nSynthetic", "gpt-4.1-mini", citations=GROUPS)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["max_output_tokens"], 500)

    async def test_provider_wall_timeout_and_no_new_calls_after_deadline(self):
        slow = AsyncMock(side_effect=lambda **kw: None)
        async def wait(**kwargs):
            await asyncio.sleep(1)
        slow.side_effect = wait
        with patch.object(execution, "PROVIDER_REQUEST_TIMEOUT", 0.01):
            with self.assertRaises(execution.ProviderTimeout):
                await execution.provider_request(slow)
        self.assertEqual(slow.await_count, 1)
        token = execution.query_deadline.set(time.monotonic() - 1)
        try:
            with self.assertRaises(execution.QueryDeadlineExceeded):
                await execution.provider_request(slow)
        finally:
            execution.query_deadline.reset(token)
        self.assertEqual(slow.await_count, 1)

    async def test_both_citation_attempts_share_deadline_and_second_timeout_shrinks(self):
        observed = []
        async def parse(**kwargs):
            observed.append((execution.query_deadline.get(), kwargs["timeout"].read, kwargs["max_output_tokens"]))
            if len(observed) == 1:
                await asyncio.sleep(0.01)
                return generated([99])
            return generated([1])
        deadline = time.monotonic() + 1
        token = execution.query_deadline.set(deadline)
        try:
            with patch.object(generator.client.responses, "parse", side_effect=parse):
                result = await generator.generate_answer("Policy?", "[SOURCE 1]\nSynthetic", "gpt-4.1-mini", citations=GROUPS)
        finally:
            execution.query_deadline.reset(token)
        self.assertEqual(result.citation_ids, [1])
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(v[0] == deadline and v[2] == 500 for v in observed))
        self.assertLess(observed[1][1], observed[0][1])

    async def test_deadline_stops_citation_retry_even_if_first_callback_ignores_cancellation(self):
        async def late(**kwargs):
            # Simulate a noncooperative callback without a real provider request.
            time.sleep(0.02)
            return generated([99])
        token = execution.query_deadline.set(time.monotonic() + 0.005)
        try:
            with patch.object(generator.client.responses, "parse", side_effect=late) as provider:
                with self.assertRaises(execution.QueryDeadlineExceeded):
                    await generator.generate_answer("Policy?", "[SOURCE 1]\nSynthetic", "gpt-4.1-mini", citations=GROUPS)
        finally:
            execution.query_deadline.reset(token)
        self.assertEqual(provider.await_count, 1)

    async def test_query_single_and_batch_use_guarded_client_ingestion_batch_stays_legacy(self):
        value = SimpleNamespace(usage=None, data=[SimpleNamespace(embedding=[1.0, 0.0])])
        with patch.object(embedder.query_client.embeddings, "create", new=AsyncMock(return_value=value)) as query, \
             patch.object(embedder.client.embeddings, "create", new=AsyncMock(return_value=value)) as legacy, \
             patch.object(embedder, "get_cached_embedding", return_value=None), \
             patch.object(embedder, "cache_embedding"):
            await embedder.create_embedding("Synthetic")
            token = execution.api_query_execution.set(True)
            try:
                await embedder.create_embeddings(["Synthetic"])
            finally:
                execution.api_query_execution.reset(token)
            await embedder.create_embeddings(["Synthetic"])  # Fake ingestion boundary, no ingestion runs.
        self.assertEqual(query.await_count, 2)
        self.assertTrue(all(c.kwargs["timeout"].read <= 20 and c.kwargs["timeout"].connect <= 5 for c in query.await_args_list))
        self.assertEqual(legacy.await_count, 1)
        self.assertNotIn("timeout", legacy.await_args.kwargs)

    async def test_expired_during_sdk_preparation_never_reaches_transport(self):
        for query_expiry in (True, False):
            sent = []
            async def transport(request):
                sent.append(True)
                return httpx2.Response(503)
            http = execution.query_http_client(transport=httpx2.MockTransport(transport))
            async def preparation(request):
                time.sleep(0.02)
            http.event_hooks["request"].insert(0, preparation)
            token = execution.query_deadline.set(time.monotonic() + 0.005 if query_expiry else None)
            try:
                async with openai.AsyncOpenAI(api_key="test-only", max_retries=0, http_client=http) as client:
                    with patch.object(execution, "PROVIDER_REQUEST_TIMEOUT", 0.005), self.assertRaises(
                            execution.QueryDeadlineExceeded if query_expiry else execution.ProviderTimeout):
                        await execution.provider_request(client.embeddings.create, model="text-embedding-3-small", input="Synthetic")
            finally:
                execution.query_deadline.reset(token)
            self.assertFalse(sent)


class APIDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app(limits=QueryLimits(query_deadline_seconds=0.05, max_active_queries=1))
        self.pipeline = AsyncMock(return_value=RESULT)
        self.app.dependency_overrides[get_pipeline] = lambda: self.pipeline
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.http.aclose)
        self.headers = {"Authorization": "Bearer " + create_access_token("test", "acme", "employee")}

    async def query(self):
        return await self.http.post("/api/v1/query", json={"query": "Policy?"}, headers=self.headers)

    async def test_async_query_deadline_returns_504_and_releases_slot(self):
        async def slow(**kw):
            await asyncio.sleep(1)
        self.pipeline.side_effect = slow
        result = await self.query()
        self.assertEqual(result.status_code, 504)
        self.assertEqual(result.json(), {"detail": "Query deadline exceeded"})
        self.assertEqual(self.app.state.query_admission.active, 0)
        self.pipeline.side_effect = None
        self.assertEqual((await self.query()).status_code, 200)

    async def test_authentication_is_inside_original_deadline(self):
        async def slow_auth():
            await asyncio.sleep(1)
        self.app.dependency_overrides[authenticated_user] = slow_auth
        self.assertEqual((await self.query()).status_code, 504)
        self.pipeline.assert_not_awaited()
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_response_validation_cannot_return_success_after_deadline(self):
        from src.api.schemas import QueryResponse
        original = QueryResponse.model_validate
        def slow(value):
            time.sleep(0.06)
            return original(value)
        with patch.object(QueryResponse, "model_validate", side_effect=slow):
            self.assertEqual((await self.query()).status_code, 504)
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_body_receive_counts_against_query_deadline(self):
        called = AsyncMock()
        limits = QueryLimits(body_timeout_seconds=1, query_deadline_seconds=0.01)
        app = QueryDeadline(QueryBodyLimit(called, limits=limits), limits=limits)
        output = []
        async def receive():
            await asyncio.sleep(1)
        async def send(message):
            output.append(message)
        await app({"type": "http", "method": "POST", "path": "/api/v1/query", "headers": []}, receive, send)
        self.assertEqual(output[0]["status"], 504)
        called.assert_not_awaited()
        self.assertIsNone(execution.query_deadline.get())

    async def test_blocking_cache_or_reranker_holds_slot_past_deadline_until_drained(self):
        for label in ("cache", "reranker"):
            started, release = Event(), Event()
            provider = AsyncMock()
            def blocking():
                started.set()
                release.wait(5)
            async def work(**kwargs):
                await execution.run_blocking(blocking)
                await execution.provider_request(provider)
                return RESULT
            self.pipeline.side_effect = work
            task = asyncio.create_task(self.query())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1), label)
                await asyncio.sleep(0.08)
                self.assertFalse(task.done(), label)
                self.assertEqual(self.app.state.query_admission.active, 1)
                self.assertEqual((await self.http.get("/health")).status_code, 200)
                with patch("src.api.main.check_ready", new=AsyncMock()):
                    self.assertEqual((await self.http.get("/ready")).status_code, 200)
                self.assertEqual((await self.query()).status_code, 503)
            finally:
                release.set()
                result = await task
            self.assertEqual(result.status_code, 504)
            provider.assert_not_awaited()
            self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_safe_provider_error_classification_no_retry(self):
        # SDK's first-use schema/import work can exceed the tiny deadline used
        # by timeout tests. This test isolates classification, not elapsed time.
        self.app = create_app(limits=QueryLimits(query_deadline_seconds=5))
        self.app.dependency_overrides[get_pipeline] = lambda: self.pipeline
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.http.aclose)
        for status in (401, 429, 503):
            seen = []
            async def fail(request):
                seen.append(1)
                return httpx2.Response(status, json={"error": {"message": "PRIVATE_PROMPT_SECRET", "type": "api_error"}})
            async with openai.AsyncOpenAI(api_key="test-only", max_retries=0,
                    http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(fail))) as client:
                async def work(**kwargs):
                    return await execution.provider_request(client.embeddings.create, model="text-embedding-3-small", input="Synthetic")
                self.pipeline.side_effect = work
                result = await self.query()
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json(), {"detail": "Generation service temporarily unavailable"})
            self.assertEqual(len(seen), 1)

    async def test_provider_timeout_maps_504_and_unknown_error_stays_500(self):
        for failure, status, message in ((execution.ProviderTimeout(), 504, "Upstream request timed out"),
                                         (RuntimeError("PRIVATE"), 500, "Query processing failed")):
            self.pipeline.side_effect = failure
            result = await self.query()
            self.assertEqual(result.status_code, status)
            self.assertEqual(result.json(), {"detail": message})
            self.assertEqual(self.app.state.query_admission.active, 0)

    def test_deadline_config_validation(self):
        with patch.dict(os.environ, {"API_QUERY_DEADLINE_SECONDS": "12"}):
            self.assertEqual(QueryLimits.from_env().query_deadline_seconds, 12)
        for value in ("0", "nan", "inf", "PRIVATE"):
            with patch.dict(os.environ, {"API_QUERY_DEADLINE_SECONDS": value}), self.assertRaises(ValueError):
                QueryLimits.from_env()
