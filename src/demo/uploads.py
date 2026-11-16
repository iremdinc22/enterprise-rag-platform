"""Demo-only upload admission; default provider gate rejects before body/Redis work."""
import asyncio
from datetime import datetime, timezone
import hashlib
from threading import Lock, BoundedSemaphore
from uuid import uuid4
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

from src.demo.upload_files import UploadRejected, UploadStorage, parse_multipart, validate_pdf
from src.demo.upload_store import canonical_uuid, validate_record


class PublishNotSent(RuntimeError):
    """Only use when it is known no broker operation has begun."""


def provider_available():
    # No enabled provider mode is implemented/approved yet. Tests inject fakes.
    return False


def publish_job(job_id):
    from src.demo.config import ACTIVE_SETTINGS
    if ACTIVE_SETTINGS is None or not provider_available(): raise PublishNotSent('Provider disabled')
    from src.workers.celery_app import celery_app
    celery_app.conf.task_publish_retry = False
    celery_app.conf.broker_transport_options = {'max_retries':0, 'socket_timeout':3, 'socket_connect_timeout':2}
    celery_app.send_task('src.demo.ingestion.ingest_document_task', args=[job_id], task_id=job_id, retry=False, queue='demo.ingestion', serializer='json')


class SubmissionResponse(BaseModel):
    document_id: str
    job_id: str
    status: Literal['submission_unknown','queued','processing','retrying','completed','failed']
    status_url: str


class JobError(BaseModel):
    code: str
    message: str


class JobResponse(BaseModel):
    job_id: str
    document_id: str
    status: Literal['submission_unknown','queued','processing','retrying','completed','failed']
    error: JobError | None


class UploadService:
    def __init__(self, settings, *, store_factory=None, publisher=publish_job, available=provider_available, validator=validate_pdf):
        from src.demo.upload_store import UploadStore
        self.settings = settings
        self.store_factory = store_factory or UploadStore.from_demo_env
        self.publisher, self.available, self.validator = publisher, available, validator
        self.storage = UploadStorage(settings.upload_root)
        self.gate = Lock()
        self.job_gate = BoundedSemaphore(4)

    def submit(self, tenant, key, body, content_type):
        filename, data = parse_multipart(body, content_type, self.settings.max_upload_bytes)
        digest = hashlib.sha256(data).hexdigest()
        store = self.store_factory()
        stored = None; keep = False
        try:
            previous = store.lookup(tenant, key, digest)
            if previous == 'conflict': raise HTTPException(409, 'Idempotency key conflict')
            if previous is not None: return public_submission(previous)
            capacity = store.capacity(tenant)
            if capacity == 'capacity': raise HTTPException(503, 'Ingestion capacity exhausted')
            if capacity == 'quota': raise HTTPException(429, 'Demo upload quota exhausted')
            if capacity != 'available': raise HTTPException(503, 'Upload temporarily unavailable')
            stats = self.validator(data, self.settings.max_pdf_pages, self.settings.max_chunks)
            document_id = 'doc-'+str(uuid4()); job_id = str(uuid4())
            record = {'tenant_id':tenant, 'document_id':document_id, 'job_id':job_id, 'filename':filename,
                'digest':digest, 'size_bytes':len(data), **stats, 'status':'submission_unknown', 'attempt':-1,
                'created_at':datetime.now(timezone.utc).isoformat()}
            validate_record(record)
            stored = self.storage.store(tenant, document_id, filename, data)
            try:
                outcome, accepted = store.admit(record, key)
            except Exception:
                # A lost Redis reply may hide an applied reservation. Never remove its file.
                keep = True
                return unknown_submission(job_id)
            if outcome == 'conflict': raise HTTPException(409, 'Idempotency key conflict')
            if outcome == 'existing': return public_submission(accepted)
            if outcome == 'capacity': raise HTTPException(503, 'Ingestion capacity exhausted')
            if outcome == 'quota': raise HTTPException(429, 'Demo upload quota exhausted')
            if outcome != 'created': raise HTTPException(503, 'Upload temporarily unavailable')
            keep = True
            try:
                self.publisher(job_id)
            except PublishNotSent:
                store.transition(job_id, 'publish_failed')
                return JSONResponse({'detail':'Submission failed','code':'submission_failed','job_id':job_id}, status_code=503)
            except Exception:
                return unknown_submission(job_id)
            try:
                _, current = store.transition(job_id, 'dispatched')
                if current is None: return unknown_submission(job_id)
            except Exception:
                return unknown_submission(job_id)
            return public_submission(current)
        finally:
            if stored is not None:
                try:
                    if not keep: stored.discard()
                finally: stored.close()
            store.close()

    def job(self, tenant, job_id):
        store = self.store_factory()
        try: record = store.get(job_id, tenant)
        finally: store.close()
        if record is None or record['tenant_id'] != tenant:
            raise HTTPException(404, 'Job not found')
        return {'job_id':record['job_id'], 'document_id':record['document_id'], 'status':record['status'],
                'error':{'code':'ingestion_failed','message':'Document could not be processed.'} if record['status']=='failed' else None}


def public_submission(record):
    return {'document_id':record['document_id'], 'job_id':record['job_id'], 'status':record['status'],
            'status_url':'/api/v1/ingestion-jobs/'+record['job_id']}


def unknown_submission(job_id):
    return JSONResponse({'detail':'Submission outcome unknown','code':'submission_unknown','job_id':job_id,
                         'status_url':'/api/v1/ingestion-jobs/'+job_id}, status_code=503)


async def receive_body(request, maximum, timeout=15):
    lengths = request.headers.getlist('content-length')
    if lengths:
        if len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise HTTPException(400, 'Invalid request')
        declared = lengths[0].lstrip('0') or '0'
        if len(declared)>len(str(maximum)) or (len(declared)==len(str(maximum)) and declared>str(maximum)):
            raise HTTPException(413, 'Upload body too large')
    data = bytearray()
    try:
        async with asyncio.timeout(timeout):
            async for chunk in request.stream():
                if len(data)+len(chunk)>maximum: raise HTTPException(413, 'Upload body too large')
                data.extend(chunk)
    except TimeoutError: raise HTTPException(408, 'Upload receive timeout') from None
    except ClientDisconnect: raise HTTPException(400, 'Upload disconnected') from None
    return bytes(data)


def install_uploads(app, settings, *, service=None):
    from src.api.dependencies import authenticated_user
    from src.auth.authorization import can_upload_document
    from src.auth.models import UserContext
    from src.rag.execution import api_query_execution, run_blocking
    service = service or UploadService(settings)
    app.state.upload_service = service

    @app.post('/api/v1/documents', status_code=202, response_model=SubmissionResponse,
        openapi_extra={'parameters':[{'name':'Idempotency-Key','in':'header','required':True,
            'schema':{'type':'string','format':'uuid'}}], 'requestBody':{'required':True,'content':{
            'multipart/form-data':{'schema':{'type':'object','required':['file'],'additionalProperties':False,
                'properties':{'file':{'type':'string','format':'binary'}}}}}}})
    async def upload(request: Request, user: UserContext = Depends(authenticated_user)):
        if not can_upload_document(user): raise HTTPException(403, 'Upload permission required')
        if user.tenant_id not in ('acme','globex'): raise HTTPException(403, 'Demo tenant not allowed')
        if request.query_params: raise HTTPException(422, 'Invalid request')
        keys = request.headers.getlist('idempotency-key')
        try:
            if len(keys)!=1: raise ValueError()
            key = canonical_uuid(keys[0])
        except ValueError: raise HTTPException(422, 'Valid Idempotency-Key required') from None
        if not service.available(): raise HTTPException(503, 'Demo provider is disabled')
        if not service.gate.acquire(blocking=False): raise HTTPException(503, 'Upload preparation capacity exhausted')
        try:
            if len(request.headers.getlist('content-type')) != 1:
                raise HTTPException(415, 'Expected one multipart PDF file')
            body = await receive_body(request, service.settings.max_upload_bytes+65536)
            token = api_query_execution.set(True)
            try:
                return await run_blocking(service.submit, user.tenant_id, key, body, request.headers.get('content-type',''))
            finally:
                api_query_execution.reset(token)
        except HTTPException: raise
        except UploadRejected as error: raise HTTPException(error.status, error.detail) from None
        except Exception: raise HTTPException(503, 'Upload temporarily unavailable') from None
        finally: service.gate.release()

    @app.get('/api/v1/ingestion-jobs/{job_id}', response_model=JobResponse)
    async def job(job_id: str, user: UserContext = Depends(authenticated_user)):
        try: job_id = canonical_uuid(job_id)
        except ValueError: raise HTTPException(404, 'Job not found') from None
        if not service.job_gate.acquire(blocking=False):
            raise HTTPException(503, 'Job status capacity exhausted')
        token = api_query_execution.set(True)
        try:
            return await run_blocking(service.job, user.tenant_id, job_id)
        except HTTPException: raise
        except Exception: raise HTTPException(503, 'Job status temporarily unavailable') from None
        finally:
            api_query_execution.reset(token)
            service.job_gate.release()
