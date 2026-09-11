from pathlib import Path

from src.ingestion.pipeline import ingest_document


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook.pdf"


chunks = ingest_document(
    file_path=pdf_path,
    document_id="employee-handbook",
    chunk_size=40,
    chunk_overlap=8
)


for chunk in chunks:
    print(f"\nChunk ID: {chunk['chunk_id']}")
    print(f"Document ID: {chunk['document_id']}")
    print(f"Filename: {chunk['filename']}")
    print(f"Page: {chunk['page']}")
    print(f"Text: {chunk['text']}")

    print(f"Embedding dimension: {len(chunk['embedding'])}")
    print(f"First 5 embedding values: {chunk['embedding'][:5]}")