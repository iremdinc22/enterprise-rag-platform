from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct
)


QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "enterprise_documents"
EMBEDDING_DIMENSION = 1536


client = QdrantClient(
    url=QDRANT_URL
)


def create_collection():
    if client.collection_exists(COLLECTION_NAME):
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=EMBEDDING_DIMENSION,
            distance=Distance.COSINE
        )
    )


def upsert_chunks(chunk_records):
    points = []

    for index, record in enumerate(chunk_records, start=1):
        point = PointStruct(
            id=index,
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

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
        wait=True
    )


def search_chunks(
    query_vector,
    limit=3
):
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        with_payload=True,
        limit=limit
    ).points

    return results