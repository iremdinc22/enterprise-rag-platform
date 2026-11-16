"""No services/providers: raw ASGI bodies and event-controlled concurrent requests."""
import asyncio
import json
import os
from threading import Event
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "resource-tests-only-00000000000000000000000")
os.environ.setdefault("OPENAI_API_KEY", "resource-tests-no-provider")

import httpx
from src.api.main import create_app
from src.api.dependencies import get_pipeline
from src.api.resource_limits import QueryBodyLimit, QueryLimits
from src.auth.jwt_handler import create_access_token
from src.rag.execution import api_query_execution, run_blocking

RESULT = {"answer": "Synthetic [1]", "citations": [{"citation_id": 1, "sources": [
    {"document_id": "policy", "filename": "acme-policy.pdf", "page": 1, "chunk_id": "chunk-001"}]}]}


class BodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, messages, *, headers=(), limit=32, timeout=0.05, receive=None, send=None):
        calls, output = [], []
        async def downstream(scope, recv, sender):
            calls.append(await recv())
            await sender({"type": "http.response.start", "status": 200, "headers": []})
            await sender({"type": "http.response.body", "body": b"ok"})
        iterator = iter(messages)
        async def recv():
            return next(iterator)
        async def sender(message):
            output.append(message)
            if send:
                await send(message)
        app = QueryBodyLimit(downstream, limits=QueryLimits(max_body_bytes=limit, body_timeout_seconds=timeout))
        await app({"type": "http", "method": "POST", "path": "/api/v1/query", "headers": list(headers)},
                  receive or recv, sender)
        return calls, output

    async def test_oversized_content_length_rejected_without_receive(self):
        recv = AsyncMock(side_effect=AssertionError("Must not consume body"))
        for length in (b"33", b"9" * 5000):
            calls, output = await self.exercise([], headers=[(b"content-length", length)], receive=recv)
            self.assertFalse(calls)
            self.assertEqual(output[0]["status"], 413)
        recv.assert_not_awaited()

    async def test_oversized_chunked_and_missing_length_count_actual_bytes(self):
        for headers in ([], [(b"transfer-encoding", b"chunked")], [(b"content-length", b"1")]):
            calls, output = await self.exercise([
                {"type": "http.request", "body": b"a" * 20, "more_body": True},
                {"type": "http.request", "body": b"b" * 13, "more_body": False}], headers=headers)
            self.assertFalse(calls)
            self.assertEqual(output[0]["status"], 413)
            self.assertEqual(json.loads(output[1]["body"]), {"detail": "Request body too large"})

    async def test_exact_byte_boundary_and_multibyte_counting(self):
        for headers in ([], [(b"content-length", b"32")]):
            calls, output = await self.exercise([
                {"type": "http.request", "body": b"a" * 16, "more_body": True},
                {"type": "http.request", "body": b"b" * 16}], headers=headers)
            self.assertEqual(output[0]["status"], 200)
            self.assertEqual(calls[0]["body"], b"a" * 16 + b"b" * 16)
        calls, output = await self.exercise([{"type": "http.request", "body": "€".encode() * 11}])
        self.assertFalse(calls)
        self.assertEqual(output[0]["status"], 413)

    async def test_slow_body_and_disconnect_never_start_app(self):
        async def slow():
            await asyncio.sleep(1)
        calls, output = await self.exercise([], receive=slow, timeout=0.01)
        self.assertFalse(calls)
        self.assertEqual(output[0]["status"], 408)
        calls, output = await self.exercise([{"type": "http.request", "body": b"x", "more_body": True},
                                             {"type": "http.disconnect"}])
        self.assertFalse(calls)
        self.assertFalse(output)

    async def test_timeout_is_total_not_reset_for_each_chunk(self):
        async def slow_chunks():
            await asyncio.sleep(0.01)
            return {"type": "http.request", "body": b"x", "more_body": True}
        calls, output = await self.exercise([], receive=slow_chunks, timeout=0.025)
        self.assertFalse(calls)
        self.assertEqual(output[0]["status"], 408)

    async def test_response_send_is_not_subject_to_body_timeout(self):
        async def slow_send(message):
            await asyncio.sleep(0.02)
        calls, output = await self.exercise([{"type": "http.request", "body": b"x" * 33}],
                                             timeout=0.01, send=slow_send)
        self.assertFalse(calls)
        self.assertEqual([m["status"] for m in output if m["type"] == "http.response.start"], [413])

    def test_defaults_and_invalid_configuration(self):
        self.assertEqual(QueryLimits(), QueryLimits(32768, 5, 2))
        with patch.dict(os.environ, {"API_MAX_BODY_BYTES": "64", "API_BODY_TIMEOUT_SECONDS": "1.5",
                                     "API_MAX_ACTIVE_QUERIES": "1"}):
            self.assertEqual(QueryLimits.from_env(), QueryLimits(64, 1.5, 1))
        for name, value in (("API_MAX_BODY_BYTES", "0"), ("API_BODY_TIMEOUT_SECONDS", "nan"),
                            ("API_MAX_ACTIVE_QUERIES", "-1"), ("API_MAX_BODY_BYTES", "PRIVATE")):
            with patch.dict(os.environ, {name: value}), self.assertRaisesRegex(ValueError, "^Invalid API resource limits$"):
                QueryLimits.from_env()


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app()
        self.provider = AsyncMock(return_value=RESULT)
        self.app.dependency_overrides[get_pipeline] = lambda: self.provider
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.http.aclose)
        self.headers = {"Authorization": "Bearer " + create_access_token("test", "acme", "employee")}

    async def query(self, **body):
        return await self.http.post("/api/v1/query", json={"query": "Policy?", **body}, headers=self.headers)

    async def test_two_active_third_rejected_and_probes_bypass_admission(self):
        started, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def work(**kwargs):
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            await release.wait()
            return RESULT
        self.provider.side_effect = work
        first, second = asyncio.create_task(self.query()), asyncio.create_task(self.query())
        try:
            await asyncio.wait_for(started.wait(), 1)
            rejected = await asyncio.wait_for(self.query(), 1)
            self.assertEqual(rejected.status_code, 503)
            self.assertEqual(rejected.json(), {"detail": "Query capacity exhausted"})
            self.assertEqual(rejected.headers["retry-after"], "1")
            self.assertEqual(self.provider.await_count, 2)
            with patch("src.api.main.check_ready", new=AsyncMock()):
                self.assertEqual((await self.http.get("/health")).status_code, 200)
                self.assertEqual((await self.http.get("/ready")).status_code, 200)
            self.assertEqual(self.app.state.query_admission.active, 2)
        finally:
            release.set()
            results = await asyncio.gather(first, second)
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_success_failure_and_cancellation_release_slots(self):
        self.assertEqual((await self.query()).status_code, 200)
        self.provider.side_effect = RuntimeError("PRIVATE")
        self.assertEqual((await self.query()).json(), {"detail": "Query processing failed"})
        started = asyncio.Event()
        async def wait(**kwargs):
            started.set()
            await asyncio.Event().wait()
        self.provider.side_effect = wait
        task = asyncio.create_task(self.query())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.app.state.query_admission.active, 0)
        self.provider.side_effect = None
        self.assertEqual((await self.query()).status_code, 200)
        self.assertFalse(api_query_execution.get())

    async def test_blocking_work_cancellation_keeps_slot_until_thread_really_finishes(self):
        started, release = Event(), Event()
        def blocking():
            started.set()
            release.wait(5)
            return RESULT
        async def work(**kwargs):
            return await run_blocking(blocking)
        self.provider.side_effect = work
        task = asyncio.create_task(self.query())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # Repeated cancellation must not abandon the worker.
            await asyncio.sleep(0)
            self.assertEqual(self.app.state.query_admission.active, 1)
            self.assertFalse(task.done())
            self.assertEqual((await asyncio.wait_for(self.http.get("/health"), 1)).status_code, 200)
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_parameters_jwt_and_tenant_identity_are_preserved(self):
        for body in ({"query": "x" * 2001}, {"retrieval_limit": 11}, {"final_limit": 4},
                     {"retrieval_limit": 2}, {"tenant_id": "globex"}):
            self.assertEqual((await self.query(**body)).status_code, 422)
        self.assertEqual((await self.http.post("/api/v1/query", json={"query": "Policy?"})).status_code, 401)
        self.provider.assert_not_awaited()
        for tenant in ("acme", "globex"):
            headers = {"Authorization": "Bearer " + create_access_token("test", tenant, "employee"),
                       "X-Tenant-ID": "other"}
            result = await self.http.post("/api/v1/query?tenant_id=other", json={"query": "x" * 2000,
                "retrieval_limit": 10, "final_limit": 3}, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.provider.await_args.kwargs["user_context"].tenant_id, tenant)
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_real_pipeline_cache_boundary_does_not_block_health_or_release_early(self):
        with patch("sentence_transformers.CrossEncoder"), patch("qdrant_client.AsyncQdrantClient"):
            from src.rag import pipeline
        started, release = Event(), Event()
        def cache(**kwargs):
            started.set()
            release.wait(5)
            return RESULT
        self.app.dependency_overrides[get_pipeline] = lambda: pipeline.run_rag
        with patch.object(pipeline, "get_cached_result", side_effect=cache), \
             patch.object(pipeline, "select_context", new=AsyncMock()) as retrieve:
            task = asyncio.create_task(self.query())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                self.assertEqual((await asyncio.wait_for(self.http.get("/health"), 1)).status_code, 200)
                task.cancel()
                await asyncio.sleep(0)
                self.assertEqual(self.app.state.query_admission.active, 1)
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            retrieve.assert_not_awaited()
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_real_reranker_boundary_leaves_probes_responsive(self):
        with patch("sentence_transformers.CrossEncoder"), patch("qdrant_client.AsyncQdrantClient"):
            from src.retrieval import context_selector
        started, release = Event(), Event()
        candidate = {"document_id": "policy", "tenant_id": "acme", "filename": "acme-policy.pdf",
                     "chunk_id": "chunk-001", "page": 1, "text": "Synthetic", "reranker_score": 1.0}
        def rerank(**kwargs):
            started.set()
            release.wait(5)
            return kwargs["candidates"]
        async def work(**kwargs):
            await context_selector.select_context("Policy?", "acme")
            return RESULT
        self.provider.side_effect = work
        with patch.object(context_selector, "hybrid_search", new=AsyncMock(return_value=[candidate])), \
             patch.object(context_selector, "rerank", side_effect=rerank), \
             patch.object(context_selector, "create_embeddings", new=AsyncMock(return_value=[[1.0, 0.0]])):
            task = asyncio.create_task(self.query())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                with patch("src.api.main.check_ready", new=AsyncMock()):
                    self.assertEqual((await asyncio.wait_for(self.http.get("/health"), 1)).status_code, 200)
                    self.assertEqual((await asyncio.wait_for(self.http.get("/ready"), 1)).status_code, 200)
            finally:
                release.set()
                self.assertEqual((await task).status_code, 200)
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_exact_default_body_boundary_is_accepted_by_fastapi(self):
        body = json.dumps({"query": "Policy?"}).encode()
        body += b" " * (32768 - len(body))
        headers = {**self.headers, "Content-Type": "application/json"}
        self.assertEqual((await self.http.post("/api/v1/query", content=body, headers=headers)).status_code, 200)
        self.assertEqual((await self.http.post("/api/v1/query", content=body + b" ", headers=headers)).status_code, 413)
        self.assertEqual(self.provider.await_count, 1)

    async def test_cancelled_thread_failure_is_drained_without_leaking_slot(self):
        started, release = Event(), Event()
        def fail():
            started.set()
            release.wait(5)
            raise RuntimeError("PRIVATE")
        async def work(**kwargs):
            return await run_blocking(fail)
        self.provider.side_effect = work
        task = asyncio.create_task(self.query())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            await asyncio.sleep(0)
            self.assertEqual(self.app.state.query_admission.active, 1)
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.app.state.query_admission.active, 0)
