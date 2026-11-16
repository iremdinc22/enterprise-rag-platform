"""Exercise the real orchestration through HTTP with external work mocked."""
import os
import contextlib
import io
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENAI_API_KEY", "api-tests-no-network")
os.environ.setdefault("JWT_SECRET_KEY", "api-tests-only-0000000000000000000000000")

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from src.api.dependencies import get_pipeline
from src.api.main import create_app
from src.auth.jwt_handler import create_access_token
from src.observability.usage_tracker import get_query_usage, record_generation_usage


class PipelineAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Patch the model constructor before pipeline import; do not download weights.
        with patch("sentence_transformers.CrossEncoder"), patch("qdrant_client.AsyncQdrantClient"):
            from src.rag import pipeline
        cls.rag = pipeline

    def setUp(self):
        self.app = create_app()
        self.app.dependency_overrides[get_pipeline] = lambda: self.rag.run_rag
        self.client = TestClient(self.app)
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer_patch = patch.object(self.rag, "get_tracer", return_value=self.provider.get_tracer("test"))
        self.tracer_patch.start()
        self.addCleanup(self.tracer_patch.stop)
        self.addCleanup(self.provider.shutdown)

    def request(self, tenant, **kwargs):
        token = create_access_token("u", tenant, "employee")
        return self.client.post("/api/v1/query", json={"query": "Same question", **kwargs},
                                headers={"Authorization": "Bearer " + token})

    def test_actual_pipeline_tenant_cache_and_usage(self):
        cache = {}
        async def retrieve(**kwargs):
            tenant = kwargs["tenant_id"]
            return [{"text": tenant, "document_id": tenant, "filename": "p.pdf", "page": 1, "chunk_id": "c"}]
        async def generate(**kwargs):
            record_generation_usage(input_tokens=10, output_tokens=2, cached_input_tokens=0, cost_usd=Decimal("0.01"))
            return SimpleNamespace(answer=kwargs["context"] + " [1]", answered=True, citation_ids=[1])
        def lookup(**kwargs):
            return cache.get(kwargs["tenant_id"])
        def write(**kwargs):
            cache[kwargs["tenant_id"]] = kwargs["result"]
        with patch.object(self.rag, "select_context", side_effect=retrieve) as retrieval, \
             patch.object(self.rag, "generate_answer", side_effect=generate), \
             patch.object(self.rag, "get_cached_result", side_effect=lookup), \
             patch.object(self.rag, "cache_result", side_effect=write):
            for tenant in ("acme", "globex", "acme"):
                response = self.request(tenant)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["citations"][0]["sources"][0]["document_id"], tenant)
            self.assertEqual(retrieval.await_count, 2)
        roots = [s for s in self.exporter.get_finished_spans() if s.name == "rag.query"]
        self.assertEqual([s.attributes["rag.total_api_tokens"] for s in roots], [12, 12, 0])
        self.assertEqual([s.attributes["rag.cache_status"] for s in roots], ["MISS", "MISS", "HIT"])
        self.assertIsNone(get_query_usage())

    def test_failure_does_not_export_exception_details(self):
        with patch.object(self.rag, "select_context", new=AsyncMock(side_effect=RuntimeError("PRIVATE-provider-data"))):
            response = self.request("acme", use_cache=False)
        self.assertEqual(response.status_code, 500)
        spans = self.exporter.get_finished_spans()
        self.assertNotIn("PRIVATE-provider-data", str([(s.events, s.status.description) for s in spans]))
        self.assertEqual([s for s in spans if s.name == "rag.query"][0].status.status_code.name, "ERROR")
        self.assertIsNone(get_query_usage())

    def test_lifespan_and_openapi(self):
        with patch("src.api.main.get_pipeline", return_value=self.rag.run_rag), \
             patch("src.api.main.configure_tracing") as tracing, TestClient(create_app()) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            tracing.assert_called_once()
            schema = client.get("/openapi.json").json()
            self.assertIn("HTTPBearer", schema["components"]["securitySchemes"])
            self.assertIn("security", schema["paths"]["/api/v1/query"]["post"])

    def test_diagnostics_do_not_print_sensitive_content(self):
        from src.generation.generator import print_validation_debug
        from src.cache import embedding_cache, result_cache
        from redis.exceptions import RedisError

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_validation_debug(
                generation=SimpleNamespace(answer="PRIVATE-answer"),
                available_source_ids={1}, attempt=1,
                validation_errors=["PRIVATE-context"],
            )
            with patch.object(embedding_cache.redis_client, "get", side_effect=RedisError("PRIVATE-credentials")):
                self.assertIsNone(embedding_cache.get_cached_embedding("PRIVATE-query", "model"))
            with patch.object(result_cache.redis_client, "get", side_effect=RedisError("PRIVATE-credentials")):
                self.assertIsNone(result_cache.get_cached_result(
                    "acme", "PRIVATE-query", "model", 5, 3, 0.5))
        self.assertNotIn("PRIVATE", output.getvalue())
