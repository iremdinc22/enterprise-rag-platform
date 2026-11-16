"""Real HTTP + Qdrant + Redis + Celery; deterministic model boundaries only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import socket
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4, uuid5, NAMESPACE_URL

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
import uvicorn

from tests.e2e.bootstrap import STATE, documents, INSTALLED

if not INSTALLED:
    raise RuntimeError("Use the dedicated E2E runner; direct suite import is forbidden")

from src.api.main import create_app
from src.api.resource_limits import QueryLimits
from src.auth.jwt_handler import create_access_token
from src.cache import result_cache, embedding_cache
from src.jobs import job_store
from src.workers.celery_app import celery_app, ingest_document_task

from src.vector_store.qdrant_store import COLLECTION_NAME, client as qdrant_client

QUESTION = "What is the annual allowance?"
SENSITIVE = "SENSITIVE_INTERNAL_PROVIDER_DETAIL"


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Register cleanup before creating resources, including partial-setup failures.
        cls.addClassCleanup(cls.cleanup)
        cls.http = httpx.Client(base_url="http://qdrant:6333", timeout=10, trust_env=False)
        cls.owns_collection = False
        cls.server = None
        cls.job_ids = []
        if cls.http.get(f"/collections/{COLLECTION_NAME}").status_code != 404:
            raise RuntimeError("Refusing to overwrite an existing E2E collection")
        r = cls.http.put(f"/collections/{COLLECTION_NAME}", json={"vectors": {"size": 1536, "distance": "Cosine"}})
        r.raise_for_status()
        cls.owns_collection = True
        cls.provider = TracerProvider()
        cls.exporter = InMemorySpanExporter()
        cls.provider.add_span_processor(SimpleSpanProcessor(cls.exporter))
        trace.set_tracer_provider(cls.provider)
        root = documents()
        deadline = time.monotonic() + 40
        while not celery_app.control.ping(timeout=1):
            if time.monotonic() > deadline:
                raise RuntimeError("E2E worker did not become ready")
        cls.histories = {}
        for tenant in ("acme", "globex"):
            job_id = str(uuid4())
            cls.job_ids.append(job_id)
            job_store.create_job(job_id, f"{tenant}-policy")
            job_store.redis_client.rpush(f"e2e:history:{job_id}", "queued")
            ingest_document_task.apply_async(args=[str(root / f"{tenant}-e2e.pdf"), f"{tenant}-policy", tenant], task_id=job_id)
            cls.wait_job(job_id, "completed")
            cls.histories[tenant] = job_store.redis_client.lrange(f"e2e:history:{job_id}", 0, -1)
        cls.loop = asyncio.new_event_loop()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        # This multi-scenario fixture exercises service integration, not demo
        # throttling. Rate/quota boundaries have separate fake-clock API tests.
        app = create_app(limits=QueryLimits(tenant_rate_limit=1000, global_rate_limit=1000,
                                          demo_max_queries=1000))
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        def serve():
            asyncio.set_event_loop(cls.loop)
            cls.loop.run_until_complete(cls.server.serve(sockets=[sock]))
        cls.thread = threading.Thread(target=serve, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 20
        while not cls.server.started:
            if not cls.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("E2E API failed startup")
            time.sleep(0.05)
        cls.api = httpx.Client(base_url=cls.url, timeout=10, trust_env=False)

    @classmethod
    def wait_job(cls, job_id, status):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                state = job_store.get_job(job_id)
                observed = job_store.redis_client.lindex(f"e2e:history:{job_id}", -1)
            except (RedisConnectionError, RedisTimeoutError):
                # Bounded readiness polling, not a retry of application assertions/tasks.
                time.sleep(0.1)
                continue
            if state.get("status") == status and observed == status:
                return state
            if state.get("status") == "failed" and status != "failed":
                raise AssertionError("E2E background ingestion failed")
            time.sleep(0.1)
        raise AssertionError("E2E background job timed out")

    @classmethod
    def cleanup(cls):
        if getattr(cls, "server", None) is not None:
            if cls.thread.is_alive():
                asyncio.run_coroutine_threadsafe(qdrant_client.close(), cls.loop).result(timeout=5)
                cls.server.should_exit = True
                cls.thread.join(timeout=10)
            cls.loop.close()
            if hasattr(cls, "api"):
                cls.api.close()
        if getattr(cls, "owns_collection", False):
            cls.http.delete(f"/collections/{COLLECTION_NAME}").raise_for_status()
            cls.owns_collection = False
        for job_id in getattr(cls, "job_ids", []):
            job_store.redis_client.delete(f"job:{job_id}", f"e2e:history:{job_id}")
        if hasattr(cls, "http"):
            cls.http.close()
        if hasattr(cls, "provider"):
            cls.provider.shutdown()

    def setUp(self):
        STATE.mode = "normal"
        STATE.inputs.clear()
        self.exporter.clear()
        # Exact test-owned cache keys, never FLUSHDB or wildcard deletion.
        for tenant in ("acme", "globex", "empty-e2e-tenant"):
            result_cache.redis_client.delete(result_cache.build_result_cache_key(
                tenant, QUESTION, "gpt-4.1-mini", 5, 3, 0.5))
        embedding_cache.redis_client.delete(embedding_cache.build_embedding_cache_key(QUESTION, "text-embedding-3-small"))

    def tearDown(self):
        STATE.mode = "normal"
        self.setUp()

    def request(self, tenant="acme", *, body=None, headers=None, token=None):
        if token is None:
            token = create_access_token("e2e-user", tenant, "employee")
        return self.api.post("/api/v1/query", json={"query": QUESTION, **(body or {})},
                             headers={"Authorization": "Bearer " + token, **(headers or {})})

    def root_spans(self):
        return [s for s in self.exporter.get_finished_spans() if s.name == "rag.query"]

    def assert_evidence(self, response, tenant):
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIn("500 USD" if tenant == "acme" else "2000 USD", result["answer"])
        self.assertNotIn("2000 USD" if tenant == "acme" else "500 USD", result["answer"])
        self.assertTrue(result["citations"])
        for group in result["citations"]:
            self.assertIn(f'[{group["citation_id"]}]', result["answer"])
            for source in group["sources"]:
                self.assertEqual(source["document_id"], f"{tenant}-policy")
                self.assertEqual(source["filename"], f"{tenant}-e2e.pdf")
                self.assertIn(source["page"], (1, 2))
                self.assertTrue(source["chunk_id"].startswith("chunk-"))

    def assert_safe_failure(self, response):
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Query processing failed"})
        self.assertNotIn(SENSITIVE, response.text)

    def test_authentication_and_validation(self):
        self.assertEqual(self.api.post("/api/v1/query", json={"query": QUESTION}).status_code, 401)
        for token in ("invalid-token", create_access_token("u", "acme", "employee", expires_minutes=-1)):
            r = self.request(token=token)
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.headers["www-authenticate"], "Bearer")
        for body in ({"query": " "}, {"retrieval_limit": 0}, {"final_limit": 99},
                     {"tenant_id": "globex"}, {"model": "client-model"}):
            self.assertEqual(self.request(body=body).status_code, 422)
        self.assertFalse(self.root_spans())

    def test_header_and_url_cannot_override_tenant(self):
        token = create_access_token("u", "acme", "employee")
        r = self.api.post("/api/v1/query?tenant_id=globex", json={"query": QUESTION},
                          headers={"Authorization": "Bearer " + token, "X-Tenant-ID": "globex"})
        self.assert_evidence(r, "acme")

    def test_real_hybrid_selection_and_tenant_citations(self):
        from src.retrieval.context_selector import select_context
        for tenant in ("acme", "globex"):
            self.assert_evidence(self.request(tenant, body={"use_cache": False}), tenant)
            selected = asyncio.run_coroutine_threadsafe(select_context(QUESTION, tenant), self.loop).result(10)
            self.assertTrue(selected)
            self.assertTrue(all(c["tenant_id"] == tenant for c in selected))
            self.assertTrue(any(c["bm25_rank"] is not None for c in selected))
            self.assertTrue(any(c["vector_rank"] is not None for c in selected))
            self.assertTrue(all("reranker_score" in c and "embedding" in c for c in selected))
            self.assertIn(tenant.upper(), STATE.inputs[-1])
            self.assertNotIn(("GLOBEX" if tenant == "acme" else "ACME"), STATE.inputs[-1])
        names = {s.name for s in self.exporter.get_finished_spans()}
        self.assertTrue({"retrieval.bm25", "retrieval.vector", "retrieval.rrf", "retrieval.reranking", "retrieval.mmr"} <= names)
        spans = self.exporter.get_finished_spans()
        self.assertTrue(all(s.attributes["retrieval.bm25_result_count"] > 0 for s in spans if s.name == "retrieval.bm25"))
        self.assertTrue(all(s.attributes["retrieval.vector_result_count"] > 0 for s in spans if s.name == "retrieval.vector"))

    def test_miss_hit_bypass_and_cross_tenant_cache(self):
        for tenant in ("acme", "globex"):
            self.assert_evidence(self.request(tenant), tenant)
        self.assertEqual(len(STATE.inputs), 2)
        for tenant in ("acme", "globex"):
            self.assert_evidence(self.request(tenant), tenant)
        self.assertEqual(len(STATE.inputs), 2)
        self.assert_evidence(self.request(body={"use_cache": False}), "acme")
        self.assertEqual(len(STATE.inputs), 3)
        roots = self.root_spans()
        self.assertEqual([s.attributes["rag.cache_status"] for s in roots], ["MISS", "MISS", "HIT", "HIT", "BYPASSED"])
        self.assertEqual([s.attributes["rag.total_api_tokens"] for s in roots][2:4], [0, 0])
        self.assertTrue(result_cache.redis_client.exists(result_cache.build_result_cache_key("acme", QUESTION, "gpt-4.1-mini", 5, 3, 0.5)))
        self.assertTrue(result_cache.redis_client.exists(result_cache.build_result_cache_key("globex", QUESTION, "gpt-4.1-mini", 5, 3, 0.5)))

    def test_concurrent_tenant_queries(self):
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda tenant: self.request(tenant, body={"use_cache": False}), ("acme", "globex")))
        for tenant, r in zip(("acme", "globex"), responses):
            self.assert_evidence(r, tenant)
        self.assertEqual(len(self.root_spans()), 2)
        self.assertTrue(all(s.attributes["rag.generation_attempts"] == 1 for s in self.root_spans()))

    def test_citation_retry_then_success(self):
        STATE.mode = "retry"
        self.assert_evidence(self.request(body={"use_cache": False}), "acme")
        self.assertEqual(len(STATE.inputs), 2)
        self.assertIn("Unknown source IDs", STATE.inputs[1])
        self.assertEqual(self.root_spans()[0].attributes["rag.generation_attempts"], 2)

    def test_citation_retry_exhaustion(self):
        STATE.mode = "invalid_citations"
        self.assert_safe_failure(self.request())
        self.assertEqual(len(STATE.inputs), 2)
        key = result_cache.build_result_cache_key("acme", QUESTION, "gpt-4.1-mini", 5, 3, 0.5)
        self.assertFalse(result_cache.redis_client.exists(key))

    def test_pdf_ingestion_and_real_background_lifecycle(self):
        from src.ingestion.pdf_parser import parse_pdf
        for tenant in ("acme", "globex"):
            pages = parse_pdf(documents() / f"{tenant}-e2e.pdf")
            self.assertEqual(len(pages), 2)
            self.assertGreater(len(pages[0]["text"].split()), 40)
            r = self.http.post(f"/collections/{COLLECTION_NAME}/points/scroll", json={
                "filter": {"must": [{"key": "tenant_id", "match": {"value": tenant}}]},
                "limit": 100, "with_payload": True, "with_vector": True})
            r.raise_for_status()
            points = r.json()["result"]["points"]
            self.assertGreater(len(points), 2)
            self.assertEqual({p["payload"]["page"] for p in points}, {1, 2})
            self.assertTrue(all(p["payload"]["tenant_id"] == tenant and len(p["vector"]) == 1536 for p in points))
            self.assertEqual(self.histories[tenant], ["queued", "processing", "completed"])
        for job_id in self.job_ids[:2]:
            self.assertEqual(job_store.get_job(job_id)["status"], "completed")

    def test_background_failure_persists(self):
        job_id = str(uuid4())
        self.job_ids.append(job_id)
        job_store.create_job(job_id, "missing-e2e-document")
        job_store.redis_client.rpush(f"e2e:history:{job_id}", "queued")
        ingest_document_task.apply_async(args=["/tmp/task028-missing.pdf", "missing-e2e-document", "acme"], task_id=job_id)
        self.wait_job(job_id, "failed")
        self.assertEqual(job_store.redis_client.lrange(f"e2e:history:{job_id}", 0, -1), ["queued", "processing", "failed"])

    def test_background_retry_lifecycle(self):
        job_id = str(uuid4())
        self.job_ids.append(job_id)
        job_store.create_job(job_id, "retry-e2e-doc")
        job_store.redis_client.rpush(f"e2e:history:{job_id}", "queued")
        ingest_document_task.apply_async(args=[str(documents() / "acme-e2e.pdf"),
                                              "retry-e2e-doc", "retry-e2e"], task_id=job_id)
        self.wait_job(job_id, "completed")
        self.assertEqual(job_store.redis_client.lrange(f"e2e:history:{job_id}", 0, -1),
                         ["queued", "processing", "retrying", "processing", "completed"])

    def test_reingestion_replaces_and_invalidates_cache(self):
        self.assert_evidence(self.request(), "acme")
        self.assert_evidence(self.request("globex"), "globex")
        key = result_cache.build_result_cache_key("acme", QUESTION, "gpt-4.1-mini", 5, 3, 0.5)
        other_key = result_cache.build_result_cache_key("globex", QUESTION, "gpt-4.1-mini", 5, 3, 0.5)
        child_key = result_cache.build_result_cache_key("acme:branch", QUESTION, "gpt-4.1-mini", 5, 3, 0.5)
        # Legacy prefix-collision keys exist only in this disposable Redis.
        result_cache.redis_client.set(child_key, "isolated-legacy-cache", ex=result_cache.RESULT_CACHE_TTL)
        self.addCleanup(result_cache.redis_client.delete, child_key)
        other_cache = result_cache.redis_client.get(other_key)
        self.assertTrue(result_cache.redis_client.exists(key))
        path = f"/collections/{COLLECTION_NAME}/points/count"
        payload = {"filter": {"must": [{"key": "tenant_id", "match": {"value": "acme"}}]}, "exact": True}
        before = self.http.post(path, json=payload).json()["result"]["count"]

        # Corrupt a test-owned target's chunk ownership. An unsafe delete-first
        # replacement would remove the conflicting point and conceal the error.
        from src.ingestion.pipeline import ingest_document
        from src.vector_store.qdrant_store import PointOwnershipConflict
        point_id = str(uuid5(NAMESPACE_URL, "acme:acme-policy:chunk-001"))
        update_path = f"/collections/{COLLECTION_NAME}/points/payload?wait=true"
        scroll_path = f"/collections/{COLLECTION_NAME}/points/scroll"
        snapshot_request = {"filter": payload["filter"], "limit": 100,
                            "with_payload": True, "with_vector": True}
        self.http.post(update_path, json={"points": [point_id], "payload": {"chunk_id": "conflicting-chunk"}}).raise_for_status()
        try:
            snapshot = self.http.post(scroll_path, json=snapshot_request)
            snapshot.raise_for_status()
            cached_before = result_cache.redis_client.get(key)
            future = asyncio.run_coroutine_threadsafe(ingest_document(
                documents() / "acme-e2e.pdf", "acme-policy", "acme"), self.loop)
            with self.assertRaises(PointOwnershipConflict):
                future.result(timeout=20)
            unchanged = self.http.post(scroll_path, json=snapshot_request)
            unchanged.raise_for_status()
            self.assertEqual(unchanged.json()["result"]["points"], snapshot.json()["result"]["points"])
            self.assertEqual(result_cache.redis_client.get(key), cached_before)
        finally:
            self.http.post(update_path, json={"points": [point_id], "payload": {"chunk_id": "chunk-001"}}).raise_for_status()

        job_id = str(uuid4())
        self.job_ids.append(job_id)
        job_store.create_job(job_id, "acme-policy")
        job_store.redis_client.rpush(f"e2e:history:{job_id}", "queued")
        ingest_document_task.apply_async(args=[str(documents() / "acme-e2e.pdf"), "acme-policy", "acme"], task_id=job_id)
        self.wait_job(job_id, "completed")
        self.assertFalse(result_cache.redis_client.exists(key))
        self.assertEqual(result_cache.redis_client.get(other_key), other_cache)
        self.assertEqual(result_cache.redis_client.get(child_key), "isolated-legacy-cache")
        after = self.http.post(path, json=payload).json()["result"]["count"]
        self.assertEqual(before, after)
        self.assert_evidence(self.request(), "acme")

    def test_qdrant_unavailable_and_timeout(self):
        for error in (ConnectionError(SENSITIVE), TimeoutError(SENSITIVE)):
            with patch.object(qdrant_client, "scroll", new=AsyncMock(side_effect=error)):
                self.assert_safe_failure(self.request(body={"use_cache": False}))
        self.assertFalse(STATE.inputs)
        with patch.object(qdrant_client, "collection_exists", new=AsyncMock(side_effect=TimeoutError(SENSITIVE))):
            self.assertEqual(self.api.get("/ready").json(), {"detail": "Service not ready"})
            self.assertEqual(self.api.get("/ready").status_code, 503)

    def test_redis_cache_failure_falls_back(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for redis in (result_cache.redis_client, embedding_cache.redis_client):
                for method in ("get", "set"):
                    stack.enter_context(patch.object(redis, method, side_effect=RedisConnectionError(SENSITIVE)))
            self.assert_evidence(self.request(), "acme")
        self.assertEqual(len(STATE.inputs), 1)

    def test_generation_failure_is_safe(self):
        STATE.mode = "generation_error"
        self.assert_safe_failure(self.request())
        self.assertEqual(len(STATE.inputs), 1)

    def test_empty_tenant_current_error_contract(self):
        self.assert_safe_failure(self.request("empty-e2e-tenant", body={"use_cache": False}))
        self.assertFalse(STATE.inputs)

    def test_empty_selected_context_current_error_contract(self):
        from src.rag import pipeline
        with patch.object(pipeline, "select_context", new=AsyncMock(return_value=[])):
            self.assert_safe_failure(self.request(body={"use_cache": False}))
        self.assertFalse(STATE.inputs)

    def test_invalid_stored_citation_metadata_is_safe(self):
        point_id = str(uuid5(NAMESPACE_URL, "acme:acme-policy:chunk-001"))
        path = f"/collections/{COLLECTION_NAME}/points/payload?wait=true"
        self.http.post(path, json={"points": [point_id], "payload": {"page": SENSITIVE}}).raise_for_status()
        try:
            self.assert_safe_failure(self.request(body={"use_cache": False}))
        finally:
            self.http.post(path, json={"points": [point_id], "payload": {"page": 1}}).raise_for_status()

    def test_provider_network_policy_blocks_real_transports(self):
        # DNS and literal-IP transports are both denied before a packet can leave.
        before = STATE.blocked
        for destination in ("api.openai.com", "huggingface.co", "8.8.8.8"):
            with self.assertRaises(PermissionError):
                socket.getaddrinfo(destination, 443)
        with socket.socket() as sock:
            with self.assertRaises(PermissionError):
                sock.connect(("8.8.8.8", 443))
        self.assertEqual(STATE.blocked - before, 4)
