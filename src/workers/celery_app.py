import asyncio

from celery import Celery

from src.ingestion.pipeline import ingest_document


celery_app = Celery(
    "enterprise_rag",
    broker="redis://localhost:6381/0"
)


@celery_app.task
def ingest_document_task(file_path, document_id):
    asyncio.run(
        ingest_document(
            file_path=file_path,
            document_id=document_id,
            chunk_size=40,
            chunk_overlap=8
        )
    )

    return f"{document_id}: ingestion completed"