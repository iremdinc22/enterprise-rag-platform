import asyncio
from pathlib import Path

from src.ingestion.pipeline import ingest_document


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook-v2.pdf"


async def main():
    chunks = await ingest_document(
        file_path=pdf_path,
        document_id="employee-handbook",
        chunk_size=40,
        chunk_overlap=8
    )

    print(f"Generated chunks: {len(chunks)}")


asyncio.run(main())