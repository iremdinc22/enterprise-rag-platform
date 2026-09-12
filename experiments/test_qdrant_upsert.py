import asyncio
from pathlib import Path

from src.ingestion.pipeline import ingest_document
from src.vector_store.qdrant_store import (
    client,
    COLLECTION_NAME
)


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook.pdf"


async def main():
    # Reset collection before ingestion
    if await client.collection_exists(COLLECTION_NAME):
        await client.delete_collection(COLLECTION_NAME)

    # Ingestion already stores chunks in Qdrant
    chunks = await ingest_document(
        file_path=pdf_path,
        document_id="employee-handbook",
        chunk_size=40,
        chunk_overlap=8
    )

    print("\n========== GENERATED CHUNKS ==========\n")

    for index, chunk in enumerate(chunks, start=1):
        print(f"Chunk #{index}")
        print(f"Chunk ID: {chunk['chunk_id']}")
        print(f"Page: {chunk['page']}")
        print(f"Text: {chunk['text']}")
        print(f"Embedding dimension: {len(chunk['embedding'])}")
        print("-" * 60)

    points, _ = await client.scroll(
        collection_name=COLLECTION_NAME,
        limit=100,
        with_payload=True,
        with_vectors=False
    )

    print("\n========== QDRANT POINTS ==========\n")

    for point in points:
        print(f"Point ID: {point.id}")
        print(f"Chunk ID: {point.payload['chunk_id']}")
        print(f"Page: {point.payload['page']}")
        print(f"Text: {point.payload['text']}")
        print("-" * 60)

    collection_info = await client.get_collection(
        COLLECTION_NAME
    )

    print("\n========== SUMMARY ==========\n")
    print(f"Chunks generated: {len(chunks)}")
    print(f"Points stored in Qdrant: {collection_info.points_count}")


asyncio.run(main())