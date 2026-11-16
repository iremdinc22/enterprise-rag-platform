"""Replacement preparation uses synthetic data and memory-only service clients."""
import os
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
import unittest
from qdrant_client.models import VectorParams, Distance
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENAI_API_KEY", "preparation-tests-no-provider")


class IngestionPreparationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch("qdrant_client.AsyncQdrantClient"):
            from src.ingestion import pipeline
            from src.vector_store import qdrant_store
        cls.pipeline, cls.store = pipeline, qdrant_store

    async def replacement(self, *, pages=None, embeddings=None, dimension=2, chunks=None):
        # Deletion/upsert would mutate this fake existing document if reached.
        old = {"old-point": {"tenant_id": "acme", "text": "Previously indexed"}}
        before = deepcopy(old)
        async def delete(**kwargs):
            old.clear()
        async def upsert(**kwargs):
            old.update({p.id: p.payload for p in kwargs["points"]})
        client = SimpleNamespace(collection_exists=AsyncMock(return_value=True),
            get_collection=AsyncMock(return_value=SimpleNamespace(config=SimpleNamespace(
                params=SimpleNamespace(vectors=VectorParams(size=dimension, distance=Distance.COSINE))))),
            retrieve=AsyncMock(return_value=[]), delete=AsyncMock(side_effect=delete),
            upsert=AsyncMock(side_effect=upsert), create_collection=AsyncMock())
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.store, "client", client))
            stack.enter_context(patch.object(self.pipeline, "parse_pdf", return_value=
                [{"page": 1, "text": "Synthetic replacement"}] if pages is None else pages))
            provider = stack.enter_context(patch.object(self.pipeline, "create_embeddings",
                new=AsyncMock(return_value=[[1.0, 0.0]] if embeddings is None else embeddings)))
            cache = stack.enter_context(patch.object(self.pipeline, "invalidate_tenant_results"))
            if chunks is not None:
                stack.enter_context(patch.object(self.pipeline, "chunk_text", return_value=chunks))
            with self.assertRaises(ValueError):
                await self.pipeline.ingest_document("/not-read/policy.pdf", "policy", "acme")
        self.assertEqual(old, before)
        client.delete.assert_not_awaited()
        client.upsert.assert_not_awaited()
        client.create_collection.assert_not_awaited()
        cache.assert_not_called()
        return provider

    async def test_empty_pdf_rejected_before_embedding(self):
        provider = await self.replacement(pages=[])
        provider.assert_not_awaited()

    async def test_zero_chunks_rejected_before_embedding(self):
        provider = await self.replacement(chunks=[])
        provider.assert_not_awaited()

    async def test_whitespace_document_rejected_before_embedding(self):
        provider = await self.replacement(pages=[{"page": 1, "text": " \n\t"}])
        provider.assert_not_awaited()

    async def test_missing_embeddings_preserve_existing_document(self):
        await self.replacement(embeddings=[])

    async def test_extra_embeddings_preserve_existing_document(self):
        await self.replacement(embeddings=[[1.0, 0.0], [0.0, 1.0]])

    async def test_malformed_embedding_batches(self):
        for value in ({}, "invalid", 123):
            with self.subTest(kind=type(value).__name__):
                await self.replacement(embeddings=value)

    async def test_wrong_vector_dimensions(self):
        for vector in ([1.0], [1.0, 0.0, 0.0]):
            with self.subTest(length=len(vector)):
                await self.replacement(embeddings=[vector])

    async def test_missing_malformed_or_non_numeric_vectors(self):
        for vector in (None, [], {}, "1,2", ["1", 0.0], [True, 0.0], [None, 0.0], [[1], 0.0]):
            with self.subTest(kind=type(vector).__name__):
                await self.replacement(embeddings=[vector])

    async def test_nonfinite_vectors(self):
        for value in (float("nan"), float("inf"), float("-inf"), 10 ** 1000):
            with self.subTest(kind=type(value).__name__):
                await self.replacement(embeddings=[[value, 0.0]])

    async def test_invalid_page_payload(self):
        for page in (0, -1, True, "1", None, 1.5):
            with self.subTest(page_type=type(page).__name__):
                await self.replacement(pages=[{"page": page, "text": "Synthetic"}])

    async def test_invalid_chunk_text_payload(self):
        for text in (None, "", " ", 123):
            with self.subTest(text_type=type(text).__name__):
                await self.replacement(chunks=[text])

    def test_complete_point_preparation_validates_required_fields(self):
        value = {"tenant_id": "acme", "document_id": "policy", "chunk_id": "chunk-001",
                 "filename": "Policy Türkçe.pdf", "page": 1, "text": "Synthetic", "embedding": [1, 0.0]}
        for field in value:
            invalid = dict(value); del invalid[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.prepare_chunk_points([invalid], dimension=2)
        for field in ("tenant_id", "document_id", "chunk_id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.prepare_chunk_points([{**value, field: "a:b"}], dimension=2)
        for malformed in (None, [], "record"):
            with self.assertRaises(ValueError):
                self.store.prepare_chunk_points([malformed], dimension=2)
        points = self.store.prepare_chunk_points([value], dimension=2)
        self.assertEqual(points[0].payload, {k: v for k, v in value.items() if k != "embedding"})
        self.assertEqual(points[0].vector, [1.0, 0.0])

    async def test_actual_collection_dimension_is_used(self):
        await self.replacement(dimension=3)

    async def test_schema_read_failure_prevents_delete(self):
        with patch.object(self.pipeline, "parse_pdf", return_value=[{"page": 1, "text": "Synthetic"}]), \
             patch.object(self.pipeline, "create_embeddings", new=AsyncMock(return_value=[[1.0, 0.0]])), \
             patch.object(self.pipeline, "get_collection_dimension", new=AsyncMock(side_effect=TimeoutError)), \
             patch.object(self.pipeline, "delete_document_chunks", new=AsyncMock()) as delete, \
             patch.object(self.pipeline, "upsert_chunks", new=AsyncMock()) as upsert, \
             patch.object(self.pipeline, "invalidate_tenant_results") as cache:
            with self.assertRaises(TimeoutError):
                await self.pipeline.ingest_document("/not-read/policy.pdf", "policy", "acme")
        delete.assert_not_awaited(); upsert.assert_not_awaited(); cache.assert_not_called()

    async def test_absent_collection_uses_creation_dimension_without_writes(self):
        client = SimpleNamespace(collection_exists=AsyncMock(return_value=False), get_collection=AsyncMock())
        with patch.object(self.store, "client", client):
            self.assertEqual(await self.store.get_collection_dimension(), self.store.EMBEDDING_DIMENSION)
        client.get_collection.assert_not_awaited()

    async def test_late_invalid_point_prevents_entire_replacement(self):
        await self.replacement(chunks=["First", "Second"], embeddings=[[1.0, 0.0], [float("nan"), 0.0]])

    async def test_named_vector_schema_is_rejected_without_writes(self):
        client = SimpleNamespace(collection_exists=AsyncMock(return_value=True),
            get_collection=AsyncMock(return_value=SimpleNamespace(config=SimpleNamespace(
                params=SimpleNamespace(vectors={"named": VectorParams(size=2, distance=Distance.COSINE)})))))
        with patch.object(self.store, "client", client), self.assertRaisesRegex(ValueError, "Unsupported collection"):
            await self.store.get_collection_dimension()

    async def test_actual_schema_and_complete_preparation_precede_delete(self):
        events = []
        original = self.store.prepare_chunk_points
        def prepare(*args, **kwargs):
            points = original(*args, **kwargs)
            events.append("prepared")
            return points
        async def delete(**kwargs):
            self.assertEqual(events, ["prepared"])
            events.append("delete")
        client = SimpleNamespace(collection_exists=AsyncMock(return_value=True),
            get_collection=AsyncMock(return_value=SimpleNamespace(config=SimpleNamespace(
                params=SimpleNamespace(vectors=VectorParams(size=2, distance=Distance.COSINE))))),
            retrieve=AsyncMock(return_value=[]), delete=AsyncMock(side_effect=delete), upsert=AsyncMock())
        with patch.object(self.store, "client", client), \
             patch.object(self.pipeline, "parse_pdf", return_value=[{"page": 1, "text": "Synthetic"}]), \
             patch.object(self.pipeline, "create_embeddings", new=AsyncMock(return_value=[[1.0, 0.0]])), \
             patch.object(self.pipeline, "prepare_chunk_points", side_effect=prepare), \
             patch.object(self.pipeline, "invalidate_tenant_results") as cache:
            result = await self.pipeline.ingest_document("/not-read/Policy Türkçe.pdf", "policy", "ACME")
        client.get_collection.assert_awaited_once_with(self.store.COLLECTION_NAME)
        client.delete.assert_awaited_once()
        client.upsert.assert_awaited_once()
        self.assertEqual(client.retrieve.await_count, 2)
        self.assertEqual(result[0]["tenant_id"], "ACME")
        self.assertEqual(result[0]["filename"], "Policy Türkçe.pdf")
        cache.assert_called_once_with(tenant_id="ACME")
