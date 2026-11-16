from uuid import uuid5, NAMESPACE_URL

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    FilterSelector
)


from src.config import QDRANT_URL, QDRANT_COLLECTION
from src.identifiers import validate_identifier

COLLECTION_NAME = QDRANT_COLLECTION
EMBEDDING_DIMENSION = 1536


client = AsyncQdrantClient(
    url=QDRANT_URL,
    timeout=10
)

_OWNERSHIP_FIELDS = ("tenant_id", "document_id", "chunk_id")
_OWNERSHIP_BATCH_SIZE = 128


class PointOwnershipConflict(ValueError):
    """An existing target ID cannot be safely attributed to the incoming tuple."""


def _point_id_and_owner(record):
    owner = tuple(validate_identifier(record.get(field)) for field in _OWNERSHIP_FIELDS)
    # Preserve the existing UUID namespace and exact serialized identity.
    point_id = str(uuid5(NAMESPACE_URL, f"{owner[0]}:{owner[1]}:{owner[2]}"))
    return point_id, owner


async def check_chunk_ownership(chunk_records):
    """Bounded target-ID reads, not a collection scan or an atomic reservation."""
    expected = {}
    for record in chunk_records:
        point_id, owner = _point_id_and_owner(record)
        if point_id in expected and expected[point_id] != owner:
            raise PointOwnershipConflict("Point ownership conflict")
        expected[point_id] = owner

    ids = list(expected)
    for start in range(0, len(ids), _OWNERSHIP_BATCH_SIZE):
        batch = ids[start:start + _OWNERSHIP_BATCH_SIZE]
        existing = await client.retrieve(
            collection_name=COLLECTION_NAME, ids=batch,
            with_payload=list(_OWNERSHIP_FIELDS), with_vectors=False,
        )
        if not isinstance(existing, list):
            raise PointOwnershipConflict("Point ownership conflict")
        seen = set()
        for point in existing:
            point_id = str(getattr(point, "id", ""))
            payload = getattr(point, "payload", None)
            if point_id not in batch or point_id in seen or not isinstance(payload, dict):
                raise PointOwnershipConflict("Point ownership conflict")
            seen.add(point_id)
            try:
                owner = tuple(validate_identifier(payload.get(field)) for field in _OWNERSHIP_FIELDS)
            except ValueError:
                raise PointOwnershipConflict("Point ownership conflict") from None
            if owner != expected[point_id]:
                raise PointOwnershipConflict("Point ownership conflict")


async def create_collection():
    if await client.collection_exists(COLLECTION_NAME):
        return

    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=EMBEDDING_DIMENSION,
            distance=Distance.COSINE
        )
    )


async def delete_document_chunks(
    document_id,
    tenant_id
):
    validate_identifier(tenant_id)
    validate_identifier(document_id)
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="tenant_id",
                        match=MatchValue(
                            value=tenant_id
                        )
                    ),
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(
                            value=document_id
                        )
                    )
                ]
            )
        ),
        wait=True
    )


async def upsert_chunks(chunk_records):
    chunk_records = list(chunk_records)
    points = []

    for record in chunk_records:
        point_id, _ = _point_id_and_owner(record)

        point = PointStruct(
            id=point_id,
            vector=record["embedding"],
            payload={
                "chunk_id": record["chunk_id"],
                "document_id": record["document_id"],
                "tenant_id": record["tenant_id"],
                "filename": record["filename"],
                "page": record["page"],
                "text": record["text"]
            }
        )

        points.append(point)

    await check_chunk_ownership(chunk_records)
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
        wait=True
    )


async def search_chunks(
    query_vector,
    tenant_id,
    limit=3
):
    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(
                        value=tenant_id
                    )
                )
            ]
        ),
        with_payload=True,
        limit=limit
    )

    return results.points
