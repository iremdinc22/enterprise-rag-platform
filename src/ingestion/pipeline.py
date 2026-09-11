from pathlib import Path

from src.ingestion.pdf_parser import parse_pdf
from src.ingestion.chunker import chunk_text


def ingest_document(
    file_path,
    document_id,
    chunk_size=40,
    chunk_overlap=8
):
    file_path = Path(file_path)

    pages = parse_pdf(file_path)

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
                "filename": file_path.name,
                "page": page["page"],
                "text": chunk
            })

            chunk_number += 1

    return chunk_records