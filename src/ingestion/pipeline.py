from pathlib import Path

from src.cache.result_cache import invalidate_tenant_results
from src.ingestion.pdf_parser import parse_pdf
from src.ingestion.chunker import chunk_text
from src.ingestion.embedder import create_embeddings
from src.vector_store.qdrant_store import (
    create_collection,
    delete_document_chunks,
    upsert_chunks
)


async def ingest_document(
    file_path,
    document_id,
    tenant_id,
    chunk_size=40,
    chunk_overlap=8
):
    file_path = Path(file_path)

    # 1. Parse PDF
    pages = parse_pdf(file_path)

    # 2. Create tenant-aware chunk records
    chunk_records = []
    chunk_number = 1

    for page in pages:
        page_chunks = chunk_text(
            page["text"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        for chunk in page_chunks:
            chunk_records.append({
                "chunk_id": f"chunk-{chunk_number:03d}",
                "document_id": document_id,
                "tenant_id": tenant_id,
                "filename": file_path.name,
                "page": page["page"],
                "text": chunk
            })

            chunk_number += 1

    # 3. Create embeddings in batch
    texts = [
        record["text"]
        for record in chunk_records
    ]

    embeddings = await create_embeddings(texts)

    # 4. Attach embeddings to chunks
    for record, embedding in zip(
        chunk_records,
        embeddings
    ):
        record["embedding"] = embedding

    # 5. Ensure the Qdrant collection exists
    await create_collection()

    # 6. Remove only this tenant's previous document version
    await delete_document_chunks(
        document_id=document_id,
        tenant_id=tenant_id
    )

    # 7. Store the new version in Qdrant
    await upsert_chunks(chunk_records)

    # 8. Invalidate stale RAG results for this tenant
    invalidate_tenant_results(
        tenant_id=tenant_id
    )

    return chunk_records