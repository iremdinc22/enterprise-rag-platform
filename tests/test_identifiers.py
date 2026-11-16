"""Shared identifier validation without real providers, PDFs, Redis or Qdrant."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid5, NAMESPACE_URL

os.environ.setdefault("JWT_SECRET_KEY", "identifier-tests-only-000000000000000000000")
os.environ.setdefault("OPENAI_API_KEY", "identifier-tests-no-provider")

import jwt
from fastapi.testclient import TestClient
from src.identifiers import validate_identifier
from src.auth import jwt_handler
from src.auth.models import UserContext
from src.api.main import create_app
from src.api.dependencies import get_pipeline

VALID = ["acme", "globex", "remote-work-policy", "employee-handbook", "second-document",
         "technical-support", "chunk-001", "ACME", "Doc_v1.2", "A" + "." * 127]
INVALID = ["", " ", "\t", " acme", "acme ", "a:b", "*", "a?b", "[ab]", "a\\b",
           "a/b", "a\x00b", "a\nb", "a\r", "a\x7fb", "é", "e\u0301", "İrem", "a" * 129,
           ".acme", "-acme", "_acme", None, 1, True, b"acme", [], {}]


def record(**updates):
    return {"tenant_id": "acme", "document_id": "employee-handbook", "chunk_id": "chunk-001",
            "filename": "Handbook Türkçe [v1].pdf", "text": "Synthetic", "page": 1,
            "embedding": [1.0] * 1536, **updates}


class IdentifierTests(unittest.TestCase):
    def test_valid_values_are_returned_exactly_without_normalization(self):
        for value in VALID:
            self.assertIs(validate_identifier(value), value)
        self.assertNotEqual(validate_identifier("ACME"), validate_identifier("acme"))

    def test_invalid_formats_types_and_boundaries_have_sanitized_error(self):
        for value in INVALID:
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(ValueError, "^Invalid identifier$"):
                validate_identifier(value)
        self.assertEqual(len(validate_identifier("a" * 128)), 128)

    def test_user_context_uses_the_same_strict_policy(self):
        for value in INVALID:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                UserContext(user_id="İrem@example.com / staff", tenant_id=value, role="employee")
        context = UserContext(user_id="İrem@example.com / staff", tenant_id="ACME", role="employee")
        self.assertEqual(context.user_id, "İrem@example.com / staff")
        self.assertEqual(context.tenant_id, "ACME")


class IdentifierJWTTests(unittest.TestCase):
    def setUp(self):
        self.secret = "identifier-fixture-signing-key-only-000000000000"
        self.patch = patch.object(jwt_handler, "SECRET_KEY", self.secret)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.pipeline = AsyncMock(return_value={"answer": "Synthetic [1]", "citations": [{
            "citation_id": 1, "sources": [{"document_id": "policy", "filename": "Policy Türkçe.pdf",
                                         "page": 1, "chunk_id": "chunk-001"}]}]})
        self.app = create_app()
        self.app.dependency_overrides[get_pipeline] = lambda: self.pipeline
        self.http = TestClient(self.app)
        self.addCleanup(self.http.close)

    def request(self, token, body=None):
        return self.http.post("/api/v1/query?tenant_id=other", json=body or {"query": "Policy?"},
            headers={"Authorization": "Bearer " + token, "X-Tenant-ID": "other"})

    def test_issuance_rejects_invalid_tenants_before_signing(self):
        with patch.object(jwt_handler.jwt, "encode") as encode:
            for value in INVALID:
                with self.assertRaisesRegex(ValueError, "^Invalid identifier$"):
                    jwt_handler.create_access_token("user@example.com", value, "employee")
        encode.assert_not_called()

    def test_signed_invalid_claims_fail_decode_and_return_safe_http_401(self):
        good = jwt.decode(jwt_handler.create_access_token("user@example.com", "acme", "employee"),
                          self.secret, algorithms=["HS256"])
        for value in INVALID:
            if isinstance(value, bytes):
                continue  # JWT JSON cannot encode bytes; direct model test covers coercion.
            token = jwt.encode({**good, "tenant_id": value}, self.secret, algorithm="HS256")
            with self.subTest(value_type=type(value).__name__), self.assertRaises((ValueError, jwt.InvalidTokenError)):
                jwt_handler.decode_access_token(token)
            result = self.request(token)
            self.assertEqual(result.status_code, 401)
            self.assertEqual(result.json(), {"detail": "Invalid or missing authentication credentials"})
            self.assertEqual(result.headers["www-authenticate"], "Bearer")
        self.pipeline.assert_not_awaited()
        self.assertEqual(self.app.state.query_admission.accepted, 0)

    def test_api_authentication_checks_identity_even_if_decoding_is_substituted(self):
        with patch("src.api.dependencies.decode_access_token", return_value=SimpleNamespace(
                user_id="user@example.com", tenant_id="acme:*", role="employee")):
            self.assertEqual(self.request("synthetic").status_code, 401)
        self.pipeline.assert_not_awaited()

    def test_valid_tenants_preserve_case_user_ids_and_cannot_be_overridden(self):
        for tenant in ("acme", "globex", "ACME"):
            token = jwt_handler.create_access_token("İrem@example.com / staff", tenant, "employee")
            decoded = jwt_handler.decode_access_token(token)
            self.assertEqual(decoded.tenant_id, tenant)
            self.assertEqual(decoded.user_id, "İrem@example.com / staff")
            result = self.request(token)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.pipeline.await_args.kwargs["user_context"].tenant_id, tenant)
            self.assertEqual(result.json()["citations"][0]["sources"][0]["filename"], "Policy Türkçe.pdf")
        self.assertEqual(self.request(token, {"query": "Policy?", "tenant_id": "acme"}).status_code, 422)


class IdentifierStoreTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch("qdrant_client.AsyncQdrantClient"):
            from src.vector_store import qdrant_store
            from src.ingestion import pipeline
        cls.store, cls.ingestion = qdrant_store, pipeline

    async def test_invalid_direct_ingestion_inputs_never_parse_embed_delete_or_write(self):
        with patch.object(self.ingestion, "parse_pdf") as parse, \
             patch.object(self.ingestion, "create_embeddings", new=AsyncMock()) as embed, \
             patch.object(self.ingestion, "create_collection", new=AsyncMock()) as create, \
             patch.object(self.ingestion, "delete_document_chunks", new=AsyncMock()) as delete, \
             patch.object(self.ingestion, "upsert_chunks", new=AsyncMock()) as upsert, \
             patch.object(self.ingestion, "invalidate_tenant_results") as invalidate:
            for field in ("tenant_id", "document_id"):
                for value in INVALID:
                    values = {"tenant_id": "acme", "document_id": "policy", field: value}
                    with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(ValueError):
                        await self.ingestion.ingest_document("/never-read.pdf", **values)
            parse.assert_not_called(); embed.assert_not_awaited(); create.assert_not_awaited()
            delete.assert_not_awaited(); upsert.assert_not_awaited(); invalidate.assert_not_called()

    async def test_invalid_upserts_including_late_batch_records_never_write(self):
        client = SimpleNamespace(upsert=AsyncMock())
        with patch.object(self.store, "client", client):
            for field in ("tenant_id", "document_id", "chunk_id"):
                for value in INVALID:
                    with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(ValueError):
                        await self.store.upsert_chunks([record(), record(**{field: value})])
                missing = record(); del missing[field]
                with self.assertRaises(ValueError):
                    await self.store.upsert_chunks([missing])
        client.upsert.assert_not_awaited()

    async def test_invalid_delete_filters_never_call_qdrant(self):
        client = SimpleNamespace(delete=AsyncMock())
        with patch.object(self.store, "client", client):
            for field in ("tenant_id", "document_id"):
                for value in INVALID:
                    values = {"tenant_id": "acme", "document_id": "policy", field: value}
                    with self.assertRaises(ValueError):
                        await self.store.delete_document_chunks(**values)
        client.delete.assert_not_awaited()

    async def test_valid_store_ids_filters_and_payloads_remain_exactly_compatible(self):
        client = SimpleNamespace(upsert=AsyncMock(), delete=AsyncMock(), retrieve=AsyncMock(return_value=[]))
        records = [record(), record(tenant_id="globex"), record(tenant_id="ACME", document_id="Doc_v1.2")]
        with patch.object(self.store, "client", client):
            await self.store.upsert_chunks(records)
            await self.store.delete_document_chunks("Doc_v1.2", "ACME")
        points = client.upsert.await_args.kwargs["points"]
        for point, value in zip(points, records):
            expected = str(uuid5(NAMESPACE_URL, f'{value["tenant_id"]}:{value["document_id"]}:{value["chunk_id"]}'))
            self.assertEqual(point.id, expected)
            self.assertEqual(point.payload, {k: v for k, v in value.items() if k != "embedding"})
        self.assertEqual(len({p.id for p in points}), 3)
        conditions = client.delete.await_args.kwargs["points_selector"].filter.must
        self.assertEqual({c.key: c.match.value for c in conditions}, {"tenant_id": "ACME", "document_id": "Doc_v1.2"})

    async def test_valid_ingestion_preserves_identifiers_and_filename_policy(self):
        with patch.object(self.ingestion, "parse_pdf", return_value=[{"page": 1, "text": "Synthetic"}]), \
             patch.object(self.ingestion, "create_embeddings", new=AsyncMock(return_value=[[1.0] * 1536])), \
             patch.object(self.ingestion, "create_collection", new=AsyncMock()), \
             patch.object(self.ingestion, "get_collection_dimension", new=AsyncMock(return_value=1536)), \
             patch.object(self.ingestion, "check_chunk_ownership", new=AsyncMock()), \
             patch.object(self.ingestion, "delete_document_chunks", new=AsyncMock()) as delete, \
             patch.object(self.ingestion, "upsert_chunks", new=AsyncMock()) as upsert, \
             patch.object(self.ingestion, "invalidate_tenant_results"):
            values = await self.ingestion.ingest_document("/never-read/Handbook Türkçe [v1].pdf", "Doc_v1.2", "ACME")
        self.assertEqual(values[0]["tenant_id"], "ACME")
        self.assertEqual(values[0]["document_id"], "Doc_v1.2")
        self.assertEqual(values[0]["chunk_id"], "chunk-001")
        self.assertEqual(values[0]["filename"], "Handbook Türkçe [v1].pdf")
        delete.assert_awaited_once_with(tenant_id="ACME", document_id="Doc_v1.2")
        upsert.assert_awaited_once_with(values)


class IdentifierWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("qdrant_client.AsyncQdrantClient"):
            from src.workers import celery_app
        cls.worker = celery_app

    def test_invalid_task_inputs_fail_without_ingestion_or_retry(self):
        with patch.object(self.worker, "ingest_document", new=AsyncMock()) as ingest, \
             patch.object(self.worker, "update_job_status") as status, \
             patch.object(self.worker.ingest_document_task, "retry") as retry:
            for field in ("tenant_id", "document_id"):
                for value in INVALID:
                    status.reset_mock()
                    values = {"tenant_id": "acme", "document_id": "policy", field: value}
                    with self.assertRaisesRegex(ValueError, "^Invalid identifier$"):
                        self.worker.ingest_document_task.run("/never-read.pdf", **values)
                    self.assertEqual(status.call_args.args[1], "failed")
                    self.assertEqual(status.call_args.kwargs["error"], "Invalid identifier")
            ingest.assert_not_awaited()
            retry.assert_not_called()

    def test_valid_task_inputs_pass_through_without_normalization(self):
        with patch.object(self.worker, "ingest_document", new=AsyncMock()) as ingest, \
             patch.object(self.worker, "update_job_status") as status:
            self.worker.ingest_document_task.run("/never-read/Handbook Türkçe [v1].pdf", "Doc_v1.2", "ACME")
        ingest.assert_awaited_once_with(file_path="/never-read/Handbook Türkçe [v1].pdf", document_id="Doc_v1.2",
                                        tenant_id="ACME", chunk_size=40, chunk_overlap=8)
        self.assertEqual(status.call_args.args[1], "completed")
