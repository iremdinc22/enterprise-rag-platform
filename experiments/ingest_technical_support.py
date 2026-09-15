import asyncio
from pathlib import Path

from src.ingestion.pipeline import ingest_document


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "technical-support.pdf"


async def main():
    chunks = await ingest_document(
        file_path=pdf_path,
        document_id="technical-support",
        chunk_size=40,
        chunk_overlap=8
    )

    print(f"Generated chunks: {len(chunks)}")

    for chunk in chunks:
        print(
            f"{chunk['document_id']} | "
            f"{chunk['chunk_id']} | "
            f"Page: {chunk['page']} | "
            f"Text: {chunk['text']}"
        )


asyncio.run(main())