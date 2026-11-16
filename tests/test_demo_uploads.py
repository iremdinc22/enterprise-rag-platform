"""Synthetic PDF + in-memory Redis/Lua; never use existing services or providers."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import fakeredis
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from fastapi.testclient import TestClient

os.environ.setdefault('JWT_SECRET_KEY','upload-tests-only-00000000000000000000000000')
from src.auth import jwt_handler
from src.demo import config
from src.demo.api import build_app
from src.demo.uploads import UploadService, PublishNotSent, receive_body
from src.demo.upload_store import UploadStore, PREFIX, ACTIVE, COUNTS
from src.demo.upload_files import inspect_pdf, validate_pdf, UploadStorage, UploadRejected, parse_multipart
from src.demo.ingestion import run_job
from tests.test_demo_infrastructure import demo_env


def pdf_bytes(text='Synthetic policy allowance evidence', pages=1, encrypted=False):
    writer=PdfWriter()
    for _ in range(pages):
        page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 10 Tf 30 750 Td ('+text+') Tj ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    if encrypted:writer.encrypt('synthetic-only')
    result=BytesIO();writer.write(result);return result.getvalue()


class UploadAPITests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('socket.socket.connect',side_effect=AssertionError('Network forbidden')))
        env=demo_env();self.stack.enter_context(patch.dict(os.environ,env,clear=True))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.settings=replace(config.DemoSettings.from_env(env),upload_root=str(self.root))
        self.stack.enter_context(patch.object(config,'ACTIVE_SETTINGS',self.settings))
        self.stack.enter_context(patch.object(jwt_handler,'SECRET_KEY',env['DEMO_JWT_SECRET']))
        self.server=fakeredis.FakeServer()
        self.redis=fakeredis.FakeRedis(server=self.server,decode_responses=True)
        self.factory=Mock(side_effect=lambda:UploadStore(fakeredis.FakeRedis(server=self.server,decode_responses=True)))
        self.publisher=Mock()
        self.validator=Mock(wraps=validate_pdf)
        self.service=UploadService(self.settings,store_factory=self.factory,publisher=self.publisher,
            available=lambda:True,validator=self.validator)
        self.app=build_app(upload_service=self.service)
        self.http=TestClient(self.app);self.addCleanup(self.http.close)
        self.pdf=pdf_bytes()

    def headers(self,tenant='acme',role='admin',key=None):
        return {'Authorization':'Bearer '+jwt_handler.create_access_token('demo',tenant,role),
                'Idempotency-Key':key or str(uuid4())}

    def upload(self,data=None,filename='policy.pdf',tenant='acme',key=None,headers=None):
        return self.http.post('/api/v1/documents',headers=headers or self.headers(tenant,key=key),
            files={'file':(filename,self.pdf if data is None else data,'application/pdf')})

    def job(self,job_id,tenant='acme'):
        return self.http.get('/api/v1/ingestion-jobs/'+job_id,headers=self.headers(tenant))

    def record(self,job_id):return UploadStore(self.redis).get(job_id)

    def test_valid_admission_generated_ids_immutable_file_and_publish(self):
        response=self.upload(filename='Policy Türkçe.pdf')
        self.assertEqual(response.status_code,202,response.text)
        body=response.json();record=self.record(body['job_id'])
        self.assertEqual(record['tenant_id'],'acme');self.assertEqual(body['status'],'queued')
        self.assertEqual(self.publisher.call_args.args,(body['job_id'],))
        path=self.root/'acme'/body['document_id']/'Policy Türkçe.pdf'
        self.assertEqual(path.read_bytes(),self.pdf)
        self.assertEqual(path.stat().st_mode&0o777,0o400)
        self.assertEqual(record['digest'],hashlib.sha256(self.pdf).hexdigest())
        self.assertNotIn(str(self.root),response.text)
        self.assertEqual(self.redis.scard(ACTIVE),1)

    def test_auth_role_and_tenant_rejections_precede_parser_and_store(self):
        for headers,status in (({'Idempotency-Key':str(uuid4())},401),
                               (self.headers(role='employee'),403),(self.headers(tenant='other'),403)):
            response=self.upload(headers=headers)
            self.assertEqual(response.status_code,status)
        self.factory.assert_not_called();self.validator.assert_not_called();self.publisher.assert_not_called()
        self.assertFalse(list(self.root.iterdir()))

    def test_bad_jwt_and_missing_idempotency_key(self):
        self.assertEqual(self.upload(headers={'Authorization':'Bearer invalid','Idempotency-Key':str(uuid4())}).status_code,401)
        headers=self.headers();headers.pop('Idempotency-Key')
        self.assertEqual(self.upload(headers=headers).status_code,422)
        headers=self.headers();headers['Idempotency-Key']='not-a-uuid'
        self.assertEqual(self.upload(headers=headers).status_code,422)
        self.factory.assert_not_called()

    def test_disabled_provider_rejects_without_receiving_or_accepting(self):
        self.service.available=lambda:False
        response=self.upload()
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json(),{'detail':'Demo provider is disabled'})
        self.factory.assert_not_called();self.validator.assert_not_called();self.publisher.assert_not_called()
        self.assertEqual(self.redis.dbsize(),0)
        self.assertFalse(list(self.root.iterdir()))

    def test_tenant_id_cannot_be_supplied_in_fields_or_url(self):
        response=self.http.post('/api/v1/documents?tenant_id=globex',headers=self.headers(),files={'file':('p.pdf',self.pdf,'application/pdf')})
        self.assertEqual(response.status_code,422)
        response=self.http.post('/api/v1/documents',headers=self.headers(),files={'file':('p.pdf',self.pdf,'application/pdf')},data={'tenant_id':'globex'})
        self.assertEqual(response.status_code,422)
        self.publisher.assert_not_called()

    def test_exactly_one_pdf_file_and_complete_multipart_required(self):
        for files in ([('file',('a.pdf',self.pdf,'application/pdf')),('file',('b.pdf',self.pdf,'application/pdf'))],
                      [('wrong',('a.pdf',self.pdf,'application/pdf'))], [('file',('a.pdf',self.pdf,'text/plain'))]):
            self.assertIn(self.http.post('/api/v1/documents',headers=self.headers(),files=files).status_code,(415,422))
        headers=self.headers();headers['Content-Type']='multipart/form-data; boundary=ABC'
        self.assertEqual(self.http.post('/api/v1/documents',headers=headers,content=b'--ABC\r\n').status_code,422)
        self.publisher.assert_not_called()

    def test_file_size_limit_is_not_just_total_body_limit(self):
        self.service.settings=replace(self.settings,max_upload_bytes=len(self.pdf)-1)
        self.assertEqual(self.upload().status_code,413)
        self.validator.assert_not_called();self.publisher.assert_not_called()

    def test_invalid_pdfs_reject_before_storage_metadata_or_publish(self):
        for data in (b'',b'not a PDF',b'%PDF-1.7\ncorrupt\n%%EOF',pdf_bytes(text=''),pdf_bytes(encrypted=True)):
            with self.subTest(size=len(data)):
                self.assertIn(self.upload(data=data).status_code,(413,422))
        self.assertEqual(self.redis.dbsize(),0);self.publisher.assert_not_called()
        self.assertFalse(list(self.root.iterdir()))

    def test_pdf_page_chunk_and_text_limits(self):
        for data in (pdf_bytes(pages=11),pdf_bytes(' '.join('word' for _ in range(6500))),pdf_bytes('x'*100001)):
            with self.subTest(size=len(data)):
                self.assertEqual(self.upload(data=data).status_code,413)
        self.assertEqual(self.redis.dbsize(),0);self.publisher.assert_not_called()

    def test_same_idempotency_file_reuses_submission_without_republish_or_quota(self):
        key=str(uuid4());first=self.upload(key=key);second=self.upload(key=key,filename='renamed.pdf')
        self.assertEqual(first.status_code,202);self.assertEqual(second.json(),first.json())
        self.publisher.assert_called_once();self.assertEqual(self.validator.call_count,1)
        self.assertEqual(self.redis.hget(COUNTS,'global'),'1')
        changed=self.upload(key=key,data=pdf_bytes('Different synthetic text'))
        self.assertEqual(changed.status_code,409)
        self.assertEqual(self.redis.hget(COUNTS,'global'),'1')

    def test_idempotency_is_tenant_scoped(self):
        key=str(uuid4());acme=self.upload(key=key);globex=self.upload(tenant='globex',key=key)
        self.assertEqual(globex.status_code,202)
        self.assertNotEqual(acme.json()['document_id'],globex.json()['document_id'])
        self.assertEqual(self.record(globex.json()['job_id'])['tenant_id'],'globex')

    def test_foreign_missing_and_invalid_jobs_are_indistinguishable(self):
        job_id=self.upload().json()['job_id']
        foreign=self.job(job_id,'globex');missing=self.job(str(uuid4()),'globex');invalid=self.job('invalid','globex')
        for result in (foreign,missing,invalid):
            self.assertEqual(result.status_code,404);self.assertEqual(result.json(),{'detail':'Job not found'})
        self.assertEqual(self.job(job_id).status_code,200)
        self.assertEqual(self.http.get('/api/v1/ingestion-jobs/'+job_id).status_code,401)

    def test_job_status_transitions_and_errors_are_sanitized(self):
        job_id=self.upload().json()['job_id'];store=UploadStore(self.redis)
        for action,attempt,state in (('claim',0,'processing'),('retrying',0,'retrying'),('claim',1,'processing'),('failed',1,'failed')):
            store.transition(job_id,action,attempt)
            response=self.job(job_id);self.assertEqual(response.json()['status'],state)
        record=self.record(job_id);record['error']='PRIVATE /path/password document content'
        self.redis.set(PREFIX+'job:'+job_id,json.dumps(record))
        response=self.job(job_id)
        self.assertEqual(response.json()['error'],{'code':'ingestion_failed','message':'Document could not be processed.'})
        self.assertNotIn('PRIVATE',response.text);self.assertNotIn('digest',response.text)
        self.assertEqual(self.redis.scard(ACTIVE),0)

    def test_global_capacity_rejects_before_pdf_validation(self):
        self.assertEqual(self.upload().status_code,202);self.assertEqual(self.upload().status_code,202)
        self.assertEqual(self.upload(tenant='globex').status_code,503)
        self.assertEqual(self.validator.call_count,2)
        self.assertEqual(self.publisher.call_count,2)
        self.assertEqual(len(list(self.root.glob('*/*/*.pdf'))),2)

    def test_tenant_and_global_lifetime_quotas(self):
        store=UploadStore(self.redis)
        for _ in range(10):
            response=self.upload();self.assertEqual(response.status_code,202)
            job_id=response.json()['job_id'];store.transition(job_id,'claim',0);store.transition(job_id,'completed',0)
        self.assertEqual(self.upload().status_code,429)
        for _ in range(10):
            response=self.upload(tenant='globex');self.assertEqual(response.status_code,202)
            job_id=response.json()['job_id'];store.transition(job_id,'claim',0);store.transition(job_id,'completed',0)
        self.assertEqual(self.upload(tenant='globex').status_code,429)
        self.assertEqual(self.redis.hget(COUNTS,'global'),'20')

    def test_uncertain_publish_keeps_reservation_and_never_republishes(self):
        self.publisher.side_effect=TimeoutError('PRIVATE broker payload')
        key=str(uuid4());response=self.upload(key=key)
        self.assertEqual(response.status_code,503);self.assertEqual(response.json()['code'],'submission_unknown')
        self.assertEqual(self.job(response.json()['job_id']).json()['status'],'submission_unknown')
        replay=self.upload(key=key);self.assertEqual(replay.status_code,202)
        self.assertEqual(replay.json()['job_id'],response.json()['job_id'])
        self.publisher.assert_called_once();self.assertEqual(self.redis.scard(ACTIVE),1)
        self.assertNotIn('PRIVATE',response.text)

    def test_definite_unsent_failure_releases_active_slot_but_not_quota(self):
        self.publisher.side_effect=PublishNotSent()
        response=self.upload();self.assertEqual(response.status_code,503)
        self.assertEqual(self.job(response.json()['job_id']).json()['status'],'failed')
        self.assertEqual(self.redis.scard(ACTIVE),0);self.assertEqual(self.redis.hget(COUNTS,'global'),'1')

    def test_worker_can_finish_before_publish_ack_without_status_downgrade(self):
        def complete(job_id):
            store=UploadStore(self.redis);store.transition(job_id,'claim',0);store.transition(job_id,'completed',0)
        self.publisher.side_effect=complete
        response=self.upload();self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['status'],'completed')
        self.assertEqual(self.redis.scard(ACTIVE),0)

    def test_unsafe_filenames_and_symlink_paths_never_overwrite(self):
        for filename in ('../p.pdf','x\\p.pdf','p.txt'):
            self.assertIn(self.upload(filename=filename).status_code,(415,422))
        outside=self.root/'outside';outside.mkdir()
        (self.root/'acme').symlink_to(outside,target_is_directory=True)
        response=self.upload();self.assertEqual(response.status_code,503)
        self.assertFalse(list(outside.iterdir()));self.publisher.assert_not_called()
        self.assertEqual(self.redis.dbsize(),0)

    def test_redis_failure_is_sanitized(self):
        self.factory.side_effect=ConnectionError('PRIVATE')
        response=self.upload();self.assertEqual(response.status_code,503)
        self.assertNotIn('PRIVATE',response.text);self.publisher.assert_not_called()
        self.assertEqual(self.job(str(uuid4())).status_code,503)

    def test_different_file_same_key_is_409_and_preserves_submission(self):
        key=str(uuid4());first=self.upload(key=key);original=first.json()
        paths={p:p.read_bytes() for p in self.root.glob('*/*/*.pdf')}
        second=self.upload(data=pdf_bytes('Different synthetic policy'),key=key)
        self.assertEqual(second.status_code,409)
        self.assertEqual({p:p.read_bytes() for p in self.root.glob('*/*/*.pdf')},paths)
        self.publisher.assert_called_once();self.assertEqual(self.redis.hget(COUNTS,'global'),'1')
        self.assertEqual(self.job(original['job_id']).status_code,200)

    def test_redis_reservation_lost_reply_keeps_file_and_never_publishes(self):
        class LostReplyStore(UploadStore):
            def admit(inner,record,key):
                super(LostReplyStore,inner).admit(record,key)
                raise TimeoutError('PRIVATE after write')
        self.service.store_factory=lambda:LostReplyStore(fakeredis.FakeRedis(server=self.server,decode_responses=True))
        key=str(uuid4());response=self.upload(key=key)
        self.assertEqual(response.status_code,503)
        job_id=response.json()['job_id'];self.assertEqual(self.record(job_id)['status'],'submission_unknown')
        self.assertEqual(len(list(self.root.glob('*/*/*.pdf'))),1)
        replay=self.upload(key=key);self.assertEqual(replay.status_code,202)
        self.assertEqual(replay.json()['job_id'],job_id);self.publisher.assert_not_called()
        self.assertEqual(self.redis.scard(ACTIVE),1)

    def test_lost_publish_reply_does_not_hide_started_worker(self):
        def lost_reply(job_id):
            UploadStore(self.redis).transition(job_id,'claim',0)
            raise TimeoutError('PRIVATE after broker accepted')
        self.publisher.side_effect=lost_reply
        response=self.upload();self.assertEqual(response.status_code,503)
        self.assertEqual(self.job(response.json()['job_id']).json()['status'],'processing')

    def test_worker_pipeline_uses_private_copy_and_duplicate_delivery_is_ignored(self):
        response=self.upload();job_id=response.json()['job_id'];calls=[]
        async def pipeline(path,record):
            self.assertNotIn(str(self.root),str(path))
            self.assertEqual(path.name,'policy.pdf');self.assertEqual(path.read_bytes(),self.pdf)
            calls.append(record['tenant_id'])
        store=UploadStore(self.redis)
        result=run_job(store,self.settings,job_id,0,available=lambda:True,pipeline=pipeline)
        self.assertEqual(result,'completed');self.assertEqual(calls,['acme'])
        self.assertEqual(run_job(store,self.settings,job_id,0,available=lambda:True,pipeline=pipeline),'ignored')
        self.assertEqual(self.job(job_id).json()['status'],'completed')
        self.assertEqual(self.redis.scard(ACTIVE),0)

    def test_worker_disabled_gate_and_file_tamper_never_call_pipeline(self):
        for disabled in (True,False):
            response=self.upload();job_id=response.json()['job_id'];record=self.record(job_id)
            if not disabled:
                path=self.root/'acme'/record['document_id']/record['filename']
                path.chmod(0o600);path.write_bytes(b'tampered synthetic file')
            pipeline=AsyncMock()
            self.assertEqual(run_job(UploadStore(self.redis),self.settings,job_id,0,available=lambda:not disabled,pipeline=pipeline),'failed')
            pipeline.assert_not_called()
            self.assertEqual(self.job(job_id).json()['status'],'failed')

    def test_worker_retry_is_bounded_and_errors_never_enter_metadata(self):
        job_id=self.upload().json()['job_id']
        async def fail(path,record):raise ConnectionError('PRIVATE document /filesystem/path')
        store=UploadStore(self.redis)
        for attempt in range(4):
            status=run_job(store,self.settings,job_id,attempt,available=lambda:True,pipeline=fail,retryable=lambda error:True)
            self.assertEqual(status,'retrying' if attempt<3 else 'failed')
            self.assertNotIn('PRIVATE',self.redis.get(PREFIX+'job:'+job_id))
        self.assertEqual(self.redis.scard(ACTIVE),0)

    def test_storage_never_replaces_existing_document_directory(self):
        response=self.upload();record=self.record(response.json()['job_id'])
        before=(self.root/'acme'/record['document_id']/'policy.pdf').read_bytes()
        with self.assertRaises(FileExistsError):
            UploadStorage(self.root).store('acme',record['document_id'],'policy.pdf',b'new')
        self.assertEqual((self.root/'acme'/record['document_id']/'policy.pdf').read_bytes(),before)

    def test_atomic_lua_idempotency_and_global_capacity_under_threads(self):
        from concurrent.futures import ThreadPoolExecutor
        from copy import deepcopy
        template=self.record(self.upload().json()['job_id'])
        def record():
            value=deepcopy(template);value.update(job_id=str(uuid4()),document_id='doc-'+str(uuid4()),status='submission_unknown',attempt=-1)
            return value
        server=fakeredis.FakeServer();key=str(uuid4())
        def reserve(_):
            store=UploadStore(fakeredis.FakeRedis(server=server,decode_responses=True))
            return store.admit(record(),key)
        with ThreadPoolExecutor(8) as pool:results=list(pool.map(reserve,range(8)))
        self.assertEqual([r[0] for r in results].count('created'),1)
        self.assertEqual(len({r[1]['job_id'] for r in results}),1)
        server=fakeredis.FakeServer()
        def distinct(_):return UploadStore(fakeredis.FakeRedis(server=server,decode_responses=True)).admit(record(),str(uuid4()))[0]
        with ThreadPoolExecutor(8) as pool:results=list(pool.map(distinct,range(8)))
        self.assertEqual(results.count('created'),2);self.assertEqual(results.count('capacity'),6)

    def test_qdrant_existing_document_guard_precedes_existing_pipeline(self):
        from src.demo.ingestion import ingest_new_document,DemoIngestionFailure
        import sys
        client=SimpleNamespace(collection_exists=AsyncMock(return_value=True),scroll=AsyncMock(return_value=([SimpleNamespace()],None)))
        ingest=AsyncMock()
        record=self.record(self.upload().json()['job_id'])
        modules={'src.vector_store.qdrant_store':SimpleNamespace(client=client,COLLECTION_NAME='isolated-demo-test'),
                 'src.ingestion.pipeline':SimpleNamespace(ingest_document=ingest)}
        with patch.dict(sys.modules,modules):
            with self.assertRaises(DemoIngestionFailure):asyncio.run(ingest_new_document(Path('/not-read/policy.pdf'),record))
        ingest.assert_not_called()
        self.assertEqual(client.scroll.call_args.kwargs['limit'],1)
        self.assertEqual({c.key:c.match.value for c in client.scroll.call_args.kwargs['scroll_filter'].must},
                         {'tenant_id':'acme','document_id':record['document_id']})
        client.scroll.return_value=([],None)
        with patch.dict(sys.modules,modules):
            asyncio.run(ingest_new_document(Path('/not-read/policy.pdf'),record))
        ingest.assert_awaited_once_with(Path('/not-read/policy.pdf'),record['document_id'],'acme')

    def test_publisher_has_no_retry_and_uses_demo_queue_fixed_task_id(self):
        import sys
        from src.demo.uploads import publish_job
        fake=SimpleNamespace(conf=SimpleNamespace(),send_task=Mock())
        with patch('src.demo.uploads.provider_available',return_value=True), \
             patch.dict(sys.modules,{'src.workers.celery_app':SimpleNamespace(celery_app=fake)}):
            job_id=str(uuid4());publish_job(job_id)
        self.assertFalse(fake.conf.task_publish_retry)
        self.assertEqual(fake.conf.broker_transport_options['max_retries'],0)
        kwargs=fake.send_task.call_args.kwargs
        self.assertEqual(kwargs['task_id'],job_id);self.assertEqual(kwargs['args'],[job_id])
        self.assertFalse(kwargs['retry']);self.assertEqual(kwargs['queue'],'demo.ingestion')


    def test_auth_and_disabled_gate_do_not_receive_body(self):
        import httpx
        async def exercise():
            async def forbidden():
                raise AssertionError('Must not receive body')
                yield b''
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://demo') as http:
                for headers,status in (({'Idempotency-Key':str(uuid4())},401),(self.headers(role='employee'),403)):
                    result=await http.post('/api/v1/documents',headers=headers,content=forbidden())
                    self.assertEqual(result.status_code,status)
                self.service.available=lambda:False
                result=await http.post('/api/v1/documents',headers=self.headers(),content=forbidden())
                self.assertEqual(result.status_code,503)
        asyncio.run(exercise())
        self.factory.assert_not_called()

    def test_cancelled_preparation_retains_slot_until_work_finishes(self):
        import httpx
        entered,release=Event(),Event()
        def prepare(*args):
            entered.set();release.wait(5)
            return {'page_count':1,'chunk_count':1,'text_characters':35}
        self.service.validator=prepare
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://demo') as http:
                pending=asyncio.create_task(http.post('/api/v1/documents',headers=self.headers(),files={'file':('p.pdf',self.pdf,'application/pdf')}))
                try:
                    while not entered.is_set():
                        if pending.done(): raise AssertionError('Preparation not reached')
                        await asyncio.sleep(.001)
                    self.assertEqual((await http.get('/health')).status_code,200)
                    pending.cancel();await asyncio.sleep(0)
                    result=await http.post('/api/v1/documents',headers=self.headers(),files={'file':('p.pdf',self.pdf,'application/pdf')})
                    self.assertEqual(result.status_code,503)
                    self.assertFalse(pending.done())
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):await pending
                self.assertFalse(self.service.gate.locked())
        asyncio.run(exercise())
        self.publisher.assert_called_once()
        self.assertEqual(self.redis.hget(COUNTS,'global'),'1')

    def test_exact_pdf_page_chunk_and_text_boundaries(self):
        self.assertEqual(inspect_pdf(pdf_bytes(pages=10))['page_count'],10)
        self.assertEqual(inspect_pdf(pdf_bytes(' '.join(['word']*6400)))['chunk_count'],200)
        self.assertEqual(inspect_pdf(pdf_bytes('x'*100000))['text_characters'],100000)

    def test_worker_shared_file_symlink_is_never_followed(self):
        response=self.upload();job_id=response.json()['job_id'];record=self.record(job_id)
        path=self.root/'acme'/record['document_id']/record['filename']
        path.unlink();outside=self.root/'outside.pdf';outside.write_bytes(self.pdf);path.symlink_to(outside)
        pipeline=AsyncMock()
        self.assertEqual(run_job(UploadStore(self.redis),self.settings,job_id,0,available=lambda:True,pipeline=pipeline),'failed')
        pipeline.assert_not_called();self.assertEqual(outside.read_bytes(),self.pdf)

    def test_no_upload_or_job_routes_on_normal_api(self):
        from src.api.main import create_app
        http=TestClient(create_app());self.addCleanup(http.close)
        self.assertEqual(http.post('/api/v1/documents',headers=self.headers(),files={'file':('p.pdf',self.pdf,'application/pdf')}).status_code,404)
        self.assertEqual(http.get('/api/v1/ingestion-jobs/'+str(uuid4()),headers=self.headers()).status_code,404)

    def test_validator_process_failure_is_safe_and_has_wall_timeout(self):
        import subprocess
        with patch('src.demo.upload_files.subprocess.run',side_effect=subprocess.TimeoutExpired('private',10)) as runner:
            with self.assertRaises(UploadRejected) as error: validate_pdf(self.pdf)
        self.assertEqual(error.exception.detail,'PDF validation failed')
        self.assertEqual(runner.call_args.kwargs['timeout'],10)
        self.assertEqual(set(runner.call_args.kwargs['env']),{'PYTHON_DOTENV_DISABLED','PYTHONDONTWRITEBYTECODE'})



    def test_foreign_job_remains_404_even_with_corrupt_other_metadata(self):
        job_id=self.upload().json()['job_id'];record=self.record(job_id)
        record['status']='PRIVATE malformed status'
        self.redis.set(PREFIX+'job:'+job_id,json.dumps(record))
        self.assertEqual(self.job(job_id,'globex').json(),{'detail':'Job not found'})
        self.assertEqual(self.job(job_id,'globex').status_code,404)
        self.assertEqual(self.job(job_id,'acme').status_code,503)

    def test_multipart_file_exact_size_and_control_filenames(self):
        import httpx
        from src.demo.upload_files import safe_filename
        for value in ('bad\0.pdf','bad\n.pdf','a'*181+'.pdf',None):
            with self.assertRaises(UploadRejected):safe_filename(value)
        request=httpx.Request('POST','http://test',files={'file':('p.pdf',b'x'*10,'application/pdf')})
        body=request.read();content_type=request.headers['content-type']
        self.assertEqual(len(parse_multipart(body,content_type,10)[1]),10)
        with self.assertRaises(UploadRejected) as error:parse_multipart(body,content_type,9)
        self.assertEqual(error.exception.status,413)

    def test_demo_celery_task_registration_preserves_bounded_retry_and_sanitizes_failure(self):
        import sys
        from src.demo.ingestion import register_worker,DemoIngestionFailure,TASK_NAME
        class App:
            conf=SimpleNamespace()
            def task(inner,**options):
                inner.options=options
                return lambda fn:fn
        app=App();store=SimpleNamespace(close=Mock())
        job_id=str(uuid4())
        taskself=SimpleNamespace(request=SimpleNamespace(id=job_id,retries=0),retry=Mock(side_effect=RuntimeError('controlled retry')))
        with patch.dict(sys.modules,{'src.workers.celery_app':SimpleNamespace(is_retryable_error=lambda error:True)}), \
             patch('src.demo.upload_store.UploadStore.from_demo_env',return_value=store), \
             patch('src.demo.ingestion.run_job',return_value='retrying') as runner, \
             patch('logging.getLogger'):
            task=register_worker(app,self.settings)
            with self.assertRaisesRegex(RuntimeError,'controlled retry'):task(taskself,job_id)
            self.assertEqual(app.options['name'],TASK_NAME);self.assertEqual(app.options['max_retries'],3)
            self.assertEqual(str(taskself.retry.call_args.kwargs['exc']),'Temporary ingestion failure')
            store.close.assert_called_once()
            runner.side_effect=ValueError('PRIVATE corpus path')
            with self.assertRaisesRegex(DemoIngestionFailure,'^Ingestion tracking unavailable$'):task(taskself,job_id)



    def test_simultaneous_idempotent_services_publish_once_and_remove_only_candidate(self):
        import httpx
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def validate(*args):
            barrier.wait(timeout=5)
            return {'page_count':1,'chunk_count':1,'text_characters':35}
        services=[UploadService(self.settings,store_factory=self.factory,publisher=self.publisher,
            available=lambda:True,validator=validate) for _ in range(2)]
        request=httpx.Request('POST','http://demo',files={'file':('policy.pdf',self.pdf,'application/pdf')})
        body=request.read();key=str(uuid4())
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda service:service.submit('acme',key,body,request.headers['content-type']),services))
        self.assertEqual(results[0]['job_id'],results[1]['job_id'])
        self.publisher.assert_called_once()
        self.assertEqual(self.redis.hget(COUNTS,'global'),'1')
        self.assertEqual(len(list(self.root.glob('*/*/*.pdf'))),1)

    def test_partial_pipeline_failure_is_not_reported_completed(self):
        job_id=self.upload().json()['job_id']
        async def partial(path,record):raise RuntimeError('PRIVATE simulated partial Qdrant write')
        self.assertEqual(run_job(UploadStore(self.redis),self.settings,job_id,0,available=lambda:True,pipeline=partial),'failed')
        response=self.job(job_id)
        self.assertEqual(response.json()['status'],'failed')
        self.assertNotIn('PRIVATE',response.text)



class UploadBodyTests(unittest.IsolatedAsyncioTestCase):
    async def request(self,messages,headers=(),timeout=.01,maximum=10,receive=None):
        from starlette.requests import Request
        iterator=iter(messages)
        async def recv():return next(iterator)
        scope={'type':'http','method':'POST','path':'/api/v1/documents','headers':headers}
        return await receive_body(Request(scope,receive or recv),maximum,timeout)

    async def test_oversized_content_length_precedes_read(self):
        from fastapi import HTTPException
        receive=AsyncMock(side_effect=AssertionError('No read'))
        with self.assertRaises(HTTPException) as error:await self.request([],[(b'content-length',b'11')],receive=receive)
        self.assertEqual(error.exception.status_code,413);receive.assert_not_called()

    async def test_actual_bytes_chunked_and_missing_length_and_underdeclared(self):
        from fastapi import HTTPException
        for headers in ([],[(b'transfer-encoding',b'chunked')],[(b'content-length',b'1')]):
            with self.assertRaises(HTTPException) as error:
                await self.request([{'type':'http.request','body':b'a'*6,'more_body':True},{'type':'http.request','body':b'b'*5}],headers)
            self.assertEqual(error.exception.status_code,413)
        self.assertEqual(await self.request([{'type':'http.request','body':b'a'*10}]),b'a'*10)

    async def test_receive_timeout_disconnect_and_bad_length(self):
        from fastapi import HTTPException
        async def slow():await asyncio.sleep(1)
        with self.assertRaises(HTTPException) as error:await self.request([],receive=slow)
        self.assertEqual(error.exception.status_code,408)
        with self.assertRaises(HTTPException) as error:await self.request([{'type':'http.disconnect'}])
        self.assertEqual(error.exception.status_code,400)
        for headers in ([(b'content-length',b'invalid')],[(b'content-length',b'1'),(b'content-length',b'1')]):
            with self.assertRaises(HTTPException) as error:await self.request([],headers)
            self.assertEqual(error.exception.status_code,400)
