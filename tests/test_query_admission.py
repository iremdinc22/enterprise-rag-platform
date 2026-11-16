"""Deterministic local rate/quota admission; no network, real services or providers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "admission-tests-only-000000000000000000000")
os.environ.setdefault("OPENAI_API_KEY", "admission-tests-no-provider")

import httpx
from fastapi import HTTPException
from src.api.main import create_app
from src.api.dependencies import get_pipeline
from src.api.resource_limits import QueryAdmission, QueryLimits
from src.auth.jwt_handler import create_access_token
from src.rag.execution import ProviderTimeout

RESULT = {"answer": "Synthetic [1]", "citations": [{"citation_id": 1, "sources": [
    {"document_id": "policy", "filename": "acme-policy.pdf", "page": 1, "chunk_id": "chunk-001"}]}]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class AdmissionPolicyTests(unittest.TestCase):
    def test_configuration_defaults_overrides_and_invalid_values(self):
        defaults = QueryLimits()
        self.assertEqual((defaults.tenant_rate_limit, defaults.global_rate_limit,
                          defaults.rate_window_seconds, defaults.demo_max_queries), (6, 12, 60, 50))
        env = {"API_TENANT_RATE_LIMIT": "2", "API_GLOBAL_RATE_LIMIT": "3",
               "API_RATE_WINDOW_SECONDS": "4.5", "API_DEMO_MAX_QUERIES": "5"}
        with patch.dict(os.environ, env):
            limits = QueryLimits.from_env()
        self.assertEqual((limits.tenant_rate_limit, limits.global_rate_limit,
                          limits.rate_window_seconds, limits.demo_max_queries), (2, 3, 4.5, 5))
        for name in env:
            for bad in ("0", "-1", "nan", "inf", "PRIVATE"):
                with self.subTest(name=name, bad=bad), patch.dict(os.environ, {name: bad}), \
                     self.assertRaisesRegex(ValueError, "^Invalid API resource limits$"):
                    QueryLimits.from_env()

    def test_rolling_window_expires_individual_requests_at_exact_boundary(self):
        clock = Clock()
        admission = QueryAdmission(QueryLimits(tenant_rate_limit=2), clock=clock)
        admission.acquire("acme"); admission.release()
        clock.advance(30)
        admission.acquire("acme"); admission.release()
        clock.advance(29.5)
        with self.assertRaises(HTTPException) as failure:
            admission.acquire("acme")
        self.assertEqual(failure.exception.headers["Retry-After"], "1")
        clock.advance(0.5)
        admission.acquire("acme"); admission.release()
        self.assertEqual(len(admission._global), 2)  # 30s-old event remains; 60s-old one expires.
        self.assertEqual(admission.accepted, 3)
        with self.assertRaises(HTTPException):
            admission.acquire("acme")
        clock.advance(30)
        admission.acquire("acme"); admission.release()
        self.assertEqual(admission.accepted, 4)

    def test_expired_tenant_state_is_pruned_and_rejected_tenants_are_not_retained(self):
        clock = Clock()
        admission = QueryAdmission(QueryLimits(global_rate_limit=1), clock=clock)
        admission.acquire("acme"); admission.release()
        for tenant in ("other1", "other2"):
            with self.assertRaises(HTTPException):
                admission.acquire(tenant)
        self.assertEqual(set(admission._tenants), {"acme"})
        clock.advance(60)
        admission.acquire("globex"); admission.release()
        self.assertEqual(set(admission._tenants), {"globex"})

    def test_atomic_capacity_has_no_waiting_queue(self):
        admission = QueryAdmission(QueryLimits(), clock=Clock())
        barrier = Barrier(12)
        def attempt(index):
            barrier.wait()
            try:
                admission.acquire(str(index))
                return 200
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(12) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(results.count(200), 2)
        self.assertEqual(results.count(503), 10)
        self.assertEqual((admission.active, admission.accepted), (2, 2))
        admission.release(); admission.release()
        self.assertEqual(admission.active, 0)

    def test_atomic_lifetime_quota_never_overshoots(self):
        admission = QueryAdmission(QueryLimits(max_active_queries=12, tenant_rate_limit=12,
            global_rate_limit=12, demo_max_queries=5), clock=Clock())
        barrier = Barrier(12)
        def attempt(index):
            barrier.wait()
            try:
                admission.acquire("acme")
                admission.release()
                return 200
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(12) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(results.count(200), 5)
        self.assertEqual(results.count(429), 7)
        self.assertEqual((admission.active, admission.accepted, len(admission._global)), (0, 5, 5))


class AdmissionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.app = create_app(admission_clock=self.clock)
        self.pipeline = AsyncMock(return_value=RESULT)
        self.app.dependency_overrides[get_pipeline] = lambda: self.pipeline
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.http.aclose)

    async def query(self, tenant="acme", *, body=None, headers=None, token=None):
        token = token if token is not None else create_access_token("test", tenant, "employee")
        return await self.http.post("/api/v1/query?tenant_id=attacker", json=body or {"query": "Policy?"},
            headers={"Authorization": "Bearer " + token, "X-Tenant-ID": "attacker", **(headers or {})})

    async def test_tenant_limits_use_verified_identity_and_do_not_charge_rejections(self):
        for _ in range(6):
            self.assertEqual((await self.query()).status_code, 200)
        rejected = await self.query()
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.json(), {"detail": "Tenant query rate limit exceeded"})
        self.assertEqual(rejected.headers["retry-after"], "60")
        self.assertEqual(self.pipeline.await_count, 6)
        self.assertEqual(self.app.state.query_admission.accepted, 6)
        self.assertEqual((await self.query("globex")).status_code, 200)
        self.assertEqual(self.pipeline.await_args.kwargs["user_context"].tenant_id, "globex")
        self.assertEqual(self.app.state.query_admission.accepted, 7)

    async def test_global_limit_and_window_expiration(self):
        for _ in range(6):
            for tenant in ("acme", "globex"):
                self.assertEqual((await self.query(tenant)).status_code, 200)
        for tenant in ("acme", "third"):
            result = await self.query(tenant)
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result.json(), {"detail": "Global query rate limit exceeded"})
        self.assertEqual(self.pipeline.await_count, 12)
        self.assertEqual(self.app.state.query_admission.accepted, 12)
        self.clock.advance(60)
        self.assertEqual((await self.query()).status_code, 200)
        self.assertEqual(self.app.state.query_admission.accepted, 13)

    async def test_default_demo_quota_is_lifetime_not_rolling_and_probes_do_not_count(self):
        for index in range(50):
            if index and index % 6 == 0:
                self.clock.advance(60)
            self.assertEqual((await self.query()).status_code, 200)
        self.clock.advance(6000)
        result = await self.query("globex")
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.json(), {"detail": "Demo query quota exhausted"})
        self.assertNotIn("retry-after", result.headers)
        self.assertEqual(self.pipeline.await_count, 50)
        with patch("src.api.main.check_ready", new=AsyncMock()):
            self.assertEqual((await self.http.get("/health")).status_code, 200)
            self.assertEqual((await self.http.get("/ready")).status_code, 200)
        self.assertEqual(self.app.state.query_admission.accepted, 50)

    async def test_auth_and_validation_failures_do_not_consume_quota_even_when_exhausted(self):
        self.app.state.query_admission.accepted = 50
        self.assertEqual((await self.query(token="invalid")).status_code, 401)
        self.assertEqual((await self.query(body={"query": "x" * 2001})).status_code, 422)
        self.assertEqual((await self.query(body={"query": "Policy?", "tenant_id": "other"})).status_code, 422)
        self.pipeline.assert_not_awaited()
        self.assertEqual(self.app.state.query_admission.accepted, 50)
        self.assertEqual(self.app.state.query_admission.active, 0)

    async def test_failures_and_cancellation_do_not_refund_accepted_queries(self):
        for failure, status in ((RuntimeError("PRIVATE"), 500), (ProviderTimeout(), 504)):
            self.pipeline.side_effect = failure
            self.assertEqual((await self.query()).status_code, status)
        started = asyncio.Event()
        async def wait(**kw):
            started.set()
            await asyncio.Event().wait()
        self.pipeline.side_effect = wait
        task = asyncio.create_task(self.query())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((self.app.state.query_admission.accepted, self.app.state.query_admission.active), (3, 0))

    async def test_capacity_rejection_precedes_quota_and_never_consumes_another_admission(self):
        admission = self.app.state.query_admission
        admission.limit = 1
        started, release = asyncio.Event(), asyncio.Event()
        async def wait(**kw):
            started.set()
            await release.wait()
            return RESULT
        self.pipeline.side_effect = wait
        task = asyncio.create_task(self.query())
        try:
            await asyncio.wait_for(started.wait(), 1)
            admission.accepted = 50  # Simultaneous exhaustion: capacity has precedence.
            self.assertEqual((await self.query()).status_code, 503)
            self.assertEqual(admission.accepted, 50)
            self.assertEqual(self.pipeline.await_count, 1)
        finally:
            release.set()
            await task
        self.assertEqual((await self.query()).status_code, 429)
        self.assertEqual(admission.active, 0)

    async def test_real_cache_hit_consumes_quota_without_retrieval_or_provider_calls(self):
        with patch("sentence_transformers.CrossEncoder"), patch("qdrant_client.AsyncQdrantClient"):
            from src.rag import pipeline
        self.app.dependency_overrides[get_pipeline] = lambda: pipeline.run_rag
        with patch.object(pipeline, "get_cached_result", return_value=RESULT), \
             patch.object(pipeline, "select_context", new=AsyncMock()) as retrieve, \
             patch.object(pipeline, "generate_answer", new=AsyncMock()) as generate:
            self.assertEqual((await self.query()).status_code, 200)
            retrieve.assert_not_awaited()
            generate.assert_not_awaited()
        self.assertEqual(self.app.state.query_admission.accepted, 1)

    async def test_failure_deadline_and_cancellation_are_never_refunded(self):
        self.app = create_app(limits=QueryLimits(query_deadline_seconds=0.02, demo_max_queries=3),
                              admission_clock=self.clock)
        self.app.dependency_overrides[get_pipeline] = lambda: self.pipeline
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.http.aclose)
        self.pipeline.side_effect = RuntimeError("PRIVATE")
        self.assertEqual((await self.query()).status_code, 500)
        async def wait(**kwargs):
            await asyncio.sleep(1)
        self.pipeline.side_effect = wait
        self.assertEqual((await self.query()).status_code, 504)
        started = asyncio.Event()
        async def cancellable(**kwargs):
            started.set()
            await asyncio.Event().wait()
        self.pipeline.side_effect = cancellable
        task = asyncio.create_task(self.query())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((self.app.state.query_admission.active, self.app.state.query_admission.accepted), (0, 3))
        result = await self.query("globex")
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.json(), {"detail": "Demo query quota exhausted"})
        self.assertEqual(self.pipeline.await_count, 3)
