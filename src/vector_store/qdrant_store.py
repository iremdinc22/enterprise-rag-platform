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


QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "enterprise_documents"
EMBEDDING_DIMENSION = 1536


client = AsyncQdrantClient(
    url=QDRANT_URL
)


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


async def delete_document_chunks(document_id):
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(
            filter=Filter(
                must=[
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
    points = []

    for record in chunk_records:
        point_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{record['document_id']}:{record['chunk_id']}"
            )
        )

        point = PointStruct(
            id=point_id,
            vector=record["embedding"],
            payload={
                "chunk_id": record["chunk_id"],
                "document_id": record["document_id"],
                "filename": record["filename"],
                "page": record["page"],
                "text": record["text"]
            }
        )

        points.append(point)

    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
        wait=True
    )


async def search_chunks(
    query_vector,
    limit=3
):
    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        with_payload=True,
        limit=limit
    )

    return results.points