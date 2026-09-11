from pathlib import Path

from src.ingestion.pdf_parser import parse_pdf
from src.ingestion.chunker import chunk_text


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook.pdf"

document_id = "employee-handbook"
filename = pdf_path.name


# Parse PDF
pages = parse_pdf(pdf_path)


# Chunk pages and propagate metadata
chunk_records = []
chunk_number = 1

for page in pages:
    page_chunks = chunk_text(
        page["text"],
        chunk_size=12,
        chunk_overlap=3
    )

    for chunk in page_chunks:
        chunk_records.append({
            "chunk_id": f"chunk-{chunk_number:03d}",
            "document_id": document_id,
            "filename": filename,
            "page": page["page"],
            "text": chunk
        })

        chunk_number += 1


# Print results
for record in chunk_records:
    print(f"\nChunk ID: {record['chunk_id']}")
    print(f"Document ID: {record['document_id']}")
    print(f"Filename: {record['filename']}")
    print(f"Page: {record['page']}")
    print(record["text"])