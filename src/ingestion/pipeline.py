from pathlib import Path

from src.identifiers import validate_identifier
from src.cache.result_cache import invalidate_tenant_results
from src.ingestion.pdf_parser import parse_pdf
from src.ingestion.chunker import chunk_text
from src.ingestion.embedder import create_embeddings
from src.vector_store.qdrant_store import (
    create_collection,
    get_collection_dimension,
    prepare_chunk_points,
    check_chunk_ownership,
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
    validate_identifier(tenant_id)
    validate_identifier(document_id)
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
                "chunk_id": validate_identifier(f"chunk-{chunk_number:03d}"),
                "document_id": document_id,
                "tenant_id": tenant_id,
                "filename": file_path.name,
                "page": page["page"],
                "text": chunk
            })

            chunk_number += 1

    if not chunk_records:
        raise ValueError("Replacement document must contain chunks")

    # 3. Create embeddings in batch
    texts = [
        record["text"]
        for record in chunk_records
    ]

    embeddings = await create_embeddings(texts)

    if not isinstance(embeddings, (list, tuple)) or len(embeddings) != len(chunk_records):
        raise ValueError("Replacement embedding count mismatch")

    # 4. Attach embeddings to chunks
    for record, embedding in zip(
        chunk_records,
        embeddings
    ):
        record["embedding"] = embedding

    # Prepare the entire replacement before any destructive operation. The same
    # pure builder is reused at the public upsert boundary.
    dimension = await get_collection_dimension()
    prepare_chunk_points(chunk_records, dimension=dimension)

    # 5. Ensure the Qdrant collection exists
    await create_collection()

    # Detect conflicting target IDs before removing the previous document.
    # upsert_chunks checks again at its own public write boundary. Neither read
    # is a transactional lock against independent/concurrent Qdrant writers.
    await check_chunk_ownership(chunk_records)

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
