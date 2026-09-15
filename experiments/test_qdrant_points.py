import asyncio

from src.vector_store.qdrant_store import (
    client,
    COLLECTION_NAME
)


async def main():
    points, _ = await client.scroll(
        collection_name=COLLECTION_NAME,
        limit=100,
        with_payload=True
    )

    print(f"Total points: {len(points)}")

    for point in points:
        print(
            f"Point ID: {point.id} | "
            f"Document: {point.payload['document_id']} | "
            f"Chunk: {point.payload['chunk_id']}"
        )


asyncio.run(main())