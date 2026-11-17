"""Tenant-owned demo jobs around existing ingestion; availability is mode-gated."""
import asyncio
import hashlib
import os
from pathlib import Path
import random
import tempfile

from src.demo.upload_files import UploadStorage, validate_pdf
from src.demo.uploads import provider_available

TASK_NAME = 'src.demo.ingestion.ingest_document_task'


class DemoIngestionFailure(RuntimeError):
    pass


async def ingest_new_document(path, record):
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from src.vector_store.qdrant_store import client, COLLECTION_NAME
    from src.ingestion.pipeline import ingest_document
    if not await client.collection_exists(COLLECTION_NAME):
        raise DemoIngestionFailure('Demo collection unavailable')
    points, _ = await client.scroll(collection_name=COLLECTION_NAME, limit=1,
        scroll_filter=Filter(must=[FieldCondition(key=field, match=MatchValue(value=record[field]))
                                   for field in ('tenant_id','document_id')]),
        with_payload=False, with_vectors=False)
    if points:
        # Includes partial writes from a prior ambiguous attempt; never replace them.
        raise DemoIngestionFailure('Document already indexed')
    await ingest_document(path, record['document_id'], record['tenant_id'])


def run_job(store, settings, job_id, attempt, *, available=provider_available,
            pipeline=ingest_new_document, validator=validate_pdf, retryable=lambda error: False):
    record = store.get(job_id)
    if record is None: return 'ignored'
    outcome, record = store.transition(job_id, 'claim', attempt)
    if outcome != 'updated': return 'ignored'
    try:
        if not available(): raise DemoIngestionFailure('Demo provider is disabled')
        data = UploadStorage(settings.upload_root).read(record, settings.max_upload_bytes)
        if len(data)!=record['size_bytes'] or hashlib.sha256(data).hexdigest()!=record['digest']:
            raise DemoIngestionFailure('Invalid upload')
        validator(data, settings.max_pdf_pages, settings.max_chunks)
        # Existing pipeline reopens a path. Use a private immutable copy so shared
        # upload path changes cannot race its parsing; preserve the display filename.
        with tempfile.TemporaryDirectory(prefix='rag-demo-ingest-') as directory:
            path = Path(directory)/record['filename']
            fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, 'wb') as stream: stream.write(data)
            asyncio.run(pipeline(path, record))
    except Exception as error:
        status = 'retrying' if attempt < 3 and retryable(error) else 'failed'
        store.transition(job_id, status, attempt)
        return status
    store.transition(job_id, 'completed', attempt)
    return 'completed'


def register_worker(celery_app, settings):
    import logging
    logging.getLogger("pypdf").setLevel(logging.CRITICAL+1)
    from src.demo.upload_store import UploadStore
    from src.workers.celery_app import is_retryable_error
    celery_app.conf.task_publish_retry = False
    celery_app.conf.accept_content = ['json']
    celery_app.conf.broker_transport_options = {'max_retries':0, 'socket_timeout':3, 'socket_connect_timeout':2}

    @celery_app.task(bind=True, name=TASK_NAME, max_retries=3, ignore_result=True)
    def ingest_document_task(self, job_id):
        store = None
        try:
            # The API publishes a fixed task ID; reject unexpected task aliases.
            if self.request.id != job_id: raise DemoIngestionFailure('Invalid ingestion job')
            store = UploadStore.from_demo_env()
            status = run_job(store, settings, job_id, self.request.retries, retryable=is_retryable_error)
        except Exception:
            raise DemoIngestionFailure('Ingestion tracking unavailable') from None
        finally:
            if store is not None: store.close()
        if status == 'retrying':
            raise self.retry(exc=DemoIngestionFailure('Temporary ingestion failure'),
                             countdown=2**self.request.retries+random.uniform(0,1))
        if status == 'failed': raise DemoIngestionFailure('Document ingestion failed')
        return status

    return ingest_document_task
