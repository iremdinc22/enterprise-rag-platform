"""Read-only, tenant-owned access to isolated demo source PDFs."""
import hashlib
from datetime import datetime
from urllib.parse import quote

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from src.demo.upload_files import UploadRejected
from src.demo.upload_store import PREFIX, canonical_uuid, validate_record
from src.identifiers import validate_identifier


def validate_document_id(value):
    validate_identifier(value)
    if not value.startswith('doc-') or value[4:] != canonical_uuid(value[4:]):
        raise ValueError('Invalid document identifier')
    return value


def owned_record(raw, tenant, expected_id):
    import json
    if raw is None:
        return None
    record = json.loads(raw)
    if not isinstance(record, dict) or record.get('tenant_id') != tenant:
        return None
    validate_record(record)
    if record['document_id'] != validate_document_id(expected_id):
        raise ValueError('Invalid document metadata')
    created = datetime.fromisoformat(record['created_at'])
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError('Invalid document metadata')
    return record


class DocumentResponse(BaseModel):
    document_id: str
    job_id: str
    filename: str
    size_bytes: int
    page_count: int
    created_at: datetime
    status: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    limit: int
    offset: int


def list_documents(service, tenant, limit, offset):
    store = service.store_factory()
    try:
        # Existing admission caps metadata at twenty documents. Bound SCAN work
        # independently, failing closed if the isolated database grows unexpectedly.
        cursor = 0
        seen = set()
        records = []
        for _ in range(64):
            cursor, keys = store.client.scan(cursor, match=PREFIX+'document:*', count=32)
            for key in keys:
                if key in seen:
                    continue
                seen.add(key)
                if len(seen) > 256:
                    raise ValueError('Metadata scan capacity exceeded')
                record = owned_record(store.client.get(key), tenant, key[len(PREFIX+'document:'):])
                if record is not None:
                    records.append(record)
            if cursor == 0:
                break
        else:
            raise ValueError('Metadata scan capacity exceeded')
        records.sort(key=lambda r: (datetime.fromisoformat(r['created_at']), r['document_id']), reverse=True)
        return {'documents':[DocumentResponse(**r) for r in records[offset:offset+limit]],
                'total':len(records), 'limit':limit, 'offset':offset}
    finally:
        store.close()


def read_document(service, tenant, identifier):
    store = service.store_factory()
    try:
        record = owned_record(store.client.get(PREFIX+'document:'+identifier), tenant, identifier)
    finally:
        store.close()
    if record is None:
        raise HTTPException(404, 'Document not found')
    try:
        # Opens every directory/file through no-follow descriptors, not FileResponse
        # path reopening. Ownership is established before any filesystem operation.
        data = service.storage.read(record, service.settings.max_upload_bytes)
    except (OSError, UploadRejected):
        raise HTTPException(404, 'Document not found') from None
    if len(data) != record['size_bytes'] or hashlib.sha256(data).hexdigest() != record['digest']:
        raise HTTPException(404, 'Document not found')
    return Response(data, media_type='application/pdf', headers={
        'Content-Disposition':"attachment; filename=\"document.pdf\"; filename*=UTF-8''"+quote(record['filename'], safe=''),
        'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':'sandbox',
    })


def install_documents(app, service):
    from src.api.dependencies import authenticated_user
    from src.auth.models import UserContext
    from src.rag.execution import api_query_execution, run_blocking

    async def execute(function, *args):
        if not service.job_gate.acquire(blocking=False):
            raise HTTPException(503, 'Document access capacity exhausted')
        token = api_query_execution.set(True)
        try:
            return await run_blocking(function, service, *args)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'Document access temporarily unavailable') from None
        finally:
            api_query_execution.reset(token)
            service.job_gate.release()

    @app.get('/api/v1/documents', response_model=DocumentListResponse)
    async def listing(response: Response, limit: int = Query(20, ge=1, le=50),
                      offset: int = Query(0, ge=0), user: UserContext = Depends(authenticated_user)):
        response.headers['Cache-Control'] = 'no-store'
        return await execute(list_documents, user.tenant_id, limit, offset)

    @app.get('/api/v1/documents/{document_id}/file', response_class=Response,
             responses={200:{'content':{'application/pdf':{}}}})
    async def file(document_id: str, user: UserContext = Depends(authenticated_user)):
        try:
            identifier = validate_document_id(document_id)
        except ValueError:
            raise HTTPException(404, 'Document not found') from None
        return await execute(read_document, user.tenant_id, identifier)
