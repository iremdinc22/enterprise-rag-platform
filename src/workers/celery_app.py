import asyncio
import random

import httpx
import openai
from celery import Celery
from qdrant_client.http.exceptions import ResponseHandlingException

from src.ingestion.pipeline import ingest_document
from src.jobs.job_store import update_job_status


celery_app = Celery(
    "enterprise_rag",
    broker="redis://localhost:6381/0"
)


RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.RateLimitError,
    httpx.ConnectError,
    httpx.TimeoutException,
    ResponseHandlingException,
)


def is_retryable_error(error):
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True

    if isinstance(error, openai.APIStatusError):
        return error.status_code >= 500

    return False


@celery_app.task(bind=True, max_retries=3)
def ingest_document_task(
    self,
    file_path,
    document_id,
    tenant_id
):
    job_id = self.request.id

    update_job_status(
        job_id,
        "processing"
    )

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

        update_job_status(
            job_id,
            "completed"
        )

        return f"{document_id}: ingestion completed"

    except FileNotFoundError as error:
        update_job_status(
            job_id,
            "failed",
            error=str(error)
        )
        raise

    except Exception as error:
        if not is_retryable_error(error):
            update_job_status(
                job_id,
                "failed",
                error=str(error)
            )
            raise

        # No retries remaining
        if self.request.retries >= self.max_retries:
            update_job_status(
                job_id,
                "failed",
                error=str(error)
            )
            raise

        # The task will be retried
        update_job_status(
            job_id,
            "retrying",
            error=str(error)
        )

        # Exponential backoff + jitter
        base_delay = 2 ** self.request.retries
        jitter = random.uniform(0, 1)
        delay = base_delay + jitter

        raise self.retry(
            exc=error,
            countdown=delay
        )