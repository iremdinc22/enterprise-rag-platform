import asyncio

from celery import Celery

from src.ingestion.pipeline import ingest_document
from src.jobs.job_store import update_job_status


celery_app = Celery(
    "enterprise_rag",
    broker="redis://localhost:6381/0"
)


@celery_app.task(bind=True, max_retries=3)
def ingest_document_task(
    self,
    file_path,
    document_id,
    tenant_id
):
    job_id = self.request.id

    update_job_status(job_id, "processing")

    try:
        asyncio.run(
            ingest_document(
                file_path=file_path,
                document_id=document_id,
                tenant_id=tenant_id,
                chunk_size=40,
                chunk_overlap=8
            )
        )

        update_job_status(job_id, "completed")

        return f"{document_id}: ingestion completed"

    except FileNotFoundError as error:
        update_job_status(
            job_id,
            "failed",
            error=str(error)
        )
        raise

    except ConnectionError as error:
        update_job_status(
            job_id,
            "retrying",
            error=str(error)
        )

        raise self.retry(
            exc=error,
            countdown=2
        )

    except Exception as error:
        update_job_status(
            job_id,
            "failed",
            error=str(error)
        )
        raise