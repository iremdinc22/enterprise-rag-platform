"""Targeted Qdrant ownership checks using memory-only clients and synthetic data."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid5, NAMESPACE_URL

os.environ.setdefault("OPENAI_API_KEY", "ownership-tests-no-provider")


def record(**updates):
    return {"tenant_id": "acme", "document_id": "remote-work-policy", "chunk_id": "chunk-001",
            "embedding": [1.0, 0.0], "filename": "acme-policy.pdf", "page": 1,
            "text": "Synthetic", **updates}


def legacy_id(value):
    return str(uuid5(NAMESPACE_URL, f'{value["tenant_id"]}:{value["document_id"]}:{value["chunk_id"]}'))


def existing(value, **updates):
    return SimpleNamespace(id=legacy_id(value), payload={key: value[key] for key in
        ("tenant_id", "document_id", "chunk_id")}, **updates)


class PointOwnershipTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch("qdrant_client.AsyncQdrantClient"):
            from src.vector_store import qdrant_store
            from src.ingestion import pipeline
        cls.store, cls.ingestion = qdrant_store, pipeline

    def client(self, result=None):
        return SimpleNamespace(retrieve=AsyncMock(return_value=[] if result is None else result),
                               upsert=AsyncMock(), delete=AsyncMock())

    async def test_legacy_uuid_and_distinct_valid_serialized_tuples_are_preserved(self):
        records = [record(), record(tenant_id="globex"), record(document_id="other-policy"),
                   record(chunk_id="chunk-002"), record(tenant_id="ACME")]
        serialized = [":".join(v[f] for f in ("tenant_id", "document_id", "chunk_id")) for v in records]
        self.assertEqual(len(set(serialized)), len(records))
        client = self.client()
        with patch.object(self.store, "client", client):
            await self.store.upsert_chunks(iter(records))  # Existing iterable callers still work.
        points = client.upsert.await_args.kwargs["points"]
        self.assertEqual([p.id for p in points], [legacy_id(r) for r in records])
        self.assertEqual(len(set(p.id for p in points)), len(records))

    async def test_same_tuple_updates_are_idempotent_and_do_not_change_point_id(self):
        value = record()
        client = self.client([existing(value)])
        with patch.object(self.store, "client", client):
            await self.store.upsert_chunks([value])
            await self.store.upsert_chunks([record(text="Updated synthetic text")])
        self.assertEqual(client.upsert.await_count, 2)
        points = [c.kwargs["points"][0] for c in client.upsert.await_args_list]
        self.assertEqual([p.id for p in points], [legacy_id(value)] * 2)
        self.assertEqual(points[1].payload["text"], "Updated synthetic text")
        client.delete.assert_not_awaited()

    async def test_conflicting_tenant_document_or_chunk_rejects_entire_upsert(self):
        for field, value in (("tenant_id", "globex"), ("document_id", "other"), ("chunk_id", "chunk-002")):
            target = existing(record()); target.payload[field] = value
            client = self.client([target])
            with self.subTest(field=field), patch.object(self.store, "client", client):
                with self.assertRaisesRegex(self.store.PointOwnershipConflict, "^Point ownership conflict$"):
                    await self.store.upsert_chunks([record()])
            client.upsert.assert_not_awaited()
            client.delete.assert_not_awaited()

    async def test_missing_or_malformed_owner_is_never_adopted(self):
        owners = [None, [], {}, {"tenant_id": "acme"}]
        for field in ("tenant_id", "document_id", "chunk_id"):
            for value in (None, "", "a:b", "acme*", "é", 123, ["acme", "globex"]):
                owner = dict(existing(record()).payload); owner[field] = value; owners.append(owner)
        for payload in owners:
            client = self.client([SimpleNamespace(id=legacy_id(record()), payload=payload)])
            with self.subTest(payload_type=type(payload).__name__), patch.object(self.store, "client", client):
                with self.assertRaises(self.store.PointOwnershipConflict):
                    await self.store.upsert_chunks([record()])
            client.upsert.assert_not_awaited()

    async def test_unexpected_or_duplicate_records_fail_closed(self):
        value = record()
        cases = [[existing(value), existing(value)], [SimpleNamespace()],
                 [existing(record(tenant_id="globex"))], None, {}]
        for result in cases:
            client = self.client(); client.retrieve.return_value = result
            with patch.object(self.store, "client", client), self.assertRaises(self.store.PointOwnershipConflict):
                await self.store.upsert_chunks([value])
            client.upsert.assert_not_awaited()

    async def test_reads_are_batched_target_ids_with_only_ownership_payload(self):
        records = [record(chunk_id=f"chunk-{i:03d}") for i in range(257)]
        client = self.client()
        with patch.object(self.store, "client", client):
            await self.store.upsert_chunks(records)
        calls = client.retrieve.await_args_list
        self.assertEqual([len(c.kwargs["ids"]) for c in calls], [128, 128, 1])
        self.assertEqual([pid for c in calls for pid in c.kwargs["ids"]], [legacy_id(r) for r in records])
        for call in calls:
            self.assertEqual(call.kwargs["with_payload"], ["tenant_id", "document_id", "chunk_id"])
            self.assertFalse(call.kwargs["with_vectors"])
        client.upsert.assert_awaited_once()

    async def test_late_batch_conflict_never_partially_upserts(self):
        records = [record(chunk_id=f"chunk-{i:03d}") for i in range(129)]
        conflict = existing(records[-1]); conflict.payload["tenant_id"] = "globex"
        client = self.client(); client.retrieve.side_effect = [[], [conflict]]
        with patch.object(self.store, "client", client), self.assertRaises(self.store.PointOwnershipConflict):
            await self.store.upsert_chunks(records)
        self.assertEqual(client.retrieve.await_count, 2)
        client.upsert.assert_not_awaited()

    async def test_conflicting_incoming_owners_at_same_target_fail_before_reads(self):
        client = self.client()
        with patch.object(self.store, "client", client), \
             patch.object(self.store, "uuid5", return_value="synthetic-forced-collision"), \
             self.assertRaises(self.store.PointOwnershipConflict):
            await self.store.upsert_chunks([record(), record(tenant_id="globex")])
        client.retrieve.assert_not_awaited()
        client.upsert.assert_not_awaited()

    async def test_invalid_input_is_rejected_before_any_ownership_read(self):
        client = self.client()
        with patch.object(self.store, "client", client):
            for field in ("tenant_id", "document_id", "chunk_id"):
                with self.assertRaisesRegex(ValueError, "^Invalid identifier$"):
                    await self.store.check_chunk_ownership([record(), record(**{field: "a:b"})])
        client.retrieve.assert_not_awaited()
        client.upsert.assert_not_awaited()

    async def test_failed_ownership_read_never_upserts(self):
        for error in (ConnectionError("PRIVATE"), TimeoutError("PRIVATE")):
            client = self.client(); client.retrieve.side_effect = error
            with patch.object(self.store, "client", client), self.assertRaises(type(error)):
                await self.store.upsert_chunks([record()])
            client.upsert.assert_not_awaited()

    def ingestion_patches(self, client):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(self.store, "client", client))
        stack.enter_context(patch.object(self.ingestion, "parse_pdf", return_value=[{"page": 1, "text": "Synthetic"}]))
        stack.enter_context(patch.object(self.ingestion, "create_embeddings", new=AsyncMock(return_value=[[1.0, 0.0]])))
        stack.enter_context(patch.object(self.ingestion, "create_collection", new=AsyncMock()))
        stack.enter_context(patch.object(self.ingestion, "get_collection_dimension", new=AsyncMock(return_value=2)))
        invalidate = stack.enter_context(patch.object(self.ingestion, "invalidate_tenant_results"))
        return stack, invalidate

    async def test_ingestion_conflict_preflight_prevents_delete_upsert_and_invalidation(self):
        conflict = existing(record()); conflict.payload["tenant_id"] = "globex"
        client = self.client([conflict])
        stack, invalidate = self.ingestion_patches(client)
        with stack, self.assertRaises(self.store.PointOwnershipConflict):
            await self.ingestion.ingest_document("/never-read/acme-policy.pdf", "remote-work-policy", "acme")
        client.delete.assert_not_awaited()
        client.upsert.assert_not_awaited()
        invalidate.assert_not_called()

    async def test_ingestion_ownership_read_failure_prevents_destructive_operations(self):
        client = self.client(); client.retrieve.side_effect = TimeoutError("PRIVATE")
        stack, invalidate = self.ingestion_patches(client)
        with stack, self.assertRaises(TimeoutError):
            await self.ingestion.ingest_document("/never-read/acme-policy.pdf", "remote-work-policy", "acme")
        client.delete.assert_not_awaited()
        client.upsert.assert_not_awaited()
        invalidate.assert_not_called()

    async def test_existing_ingestion_replacement_order_and_legacy_uuid_are_preserved(self):
        events = []
        async def retrieve(**kwargs):
            events.append("retrieve")
            return [existing(record())] if events == ["retrieve"] else []
        async def delete(**kwargs):
            events.append("delete")
        async def upsert(**kwargs):
            events.append("upsert")
        client = self.client(); client.retrieve.side_effect = retrieve
        client.delete.side_effect = delete; client.upsert.side_effect = upsert
        stack, invalidate = self.ingestion_patches(client)
        with stack:
            invalidate.side_effect = lambda **kwargs: events.append("invalidate")
            result = await self.ingestion.ingest_document("/never-read/acme-policy.pdf", "remote-work-policy", "acme")
        self.assertEqual(events, ["retrieve", "delete", "retrieve", "upsert", "invalidate"])
        self.assertEqual(client.upsert.await_args.kwargs["points"][0].id, legacy_id(record()))
        self.assertEqual(result[0]["tenant_id"], "acme")
        conditions = client.delete.await_args.kwargs["points_selector"].filter.must
        self.assertEqual({c.key: c.match.value for c in conditions}, {"tenant_id": "acme", "document_id": "remote-work-policy"})

    async def test_second_check_rejects_racing_owner_but_cannot_undo_prior_delete(self):
        conflict = existing(record()); conflict.payload["tenant_id"] = "globex"
        client = self.client(); client.retrieve.side_effect = [[], [conflict]]
        stack, invalidate = self.ingestion_patches(client)
        with stack, self.assertRaises(self.store.PointOwnershipConflict):
            await self.ingestion.ingest_document("/never-read/acme-policy.pdf", "remote-work-policy", "acme")
        # Explicitly record the nontransactional limitation rather than claiming CAS.
        client.delete.assert_awaited_once()
        client.upsert.assert_not_awaited()
        invalidate.assert_not_called()
