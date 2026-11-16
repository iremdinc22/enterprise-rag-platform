"""Network-free document access using disposable files and in-memory Redis."""
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests import test_demo_uploads as upload_fixtures
from src.demo.upload_store import PREFIX, UploadStore


class DocumentTests(unittest.TestCase):
    setUp = upload_fixtures.UploadAPITests.setUp
    headers = upload_fixtures.UploadAPITests.headers
    upload = upload_fixtures.UploadAPITests.upload
    record = upload_fixtures.UploadAPITests.record

    def listing(self, tenant='acme', query=''):
        return self.http.get('/api/v1/documents'+query, headers=self.headers(tenant))

    def file(self, identifier, tenant='acme'):
        return self.http.get('/api/v1/documents/'+identifier+'/file', headers=self.headers(tenant))

    def accepted(self, tenant='acme', filename='policy.pdf'):
        result=self.upload(tenant=tenant,filename=filename)
        self.assertEqual(result.status_code,202,result.text)
        return result.json()

    def test_empty_and_authenticated(self):
        self.assertEqual(self.listing().json(),{'documents':[],'total':0,'limit':20,'offset':0})
        for path in ('/api/v1/documents','/api/v1/documents/doc-'+str(uuid4())+'/file'):
            self.assertEqual(self.http.get(path).status_code,401)
        self.factory.reset_mock()
        self.assertEqual(self.http.get('/api/v1/documents',headers={'Authorization':'Bearer forged'}).status_code,401)
        self.factory.assert_not_called()

    def test_listing_tenant_scope_and_public_fields(self):
        acme=self.accepted(); globex=self.accepted('globex')
        for tenant, expected in (('acme',acme),('globex',globex)):
            response=self.listing(tenant);self.assertEqual(response.status_code,200,response.text)
            body=response.json();self.assertEqual(body['total'],1)
            row=body['documents'][0]
            self.assertEqual(row['document_id'],expected['document_id'])
            self.assertEqual(set(row),{'document_id','job_id','filename','size_bytes','page_count','created_at','status'})
            self.assertEqual(row['status'],'queued');self.assertEqual(row['size_bytes'],len(self.pdf))
            self.assertNotIn(str(self.root),response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')

    def test_pagination_and_status_transitions(self):
        first=self.accepted();store=UploadStore(self.redis)
        store.transition(first['job_id'],'claim',0);store.transition(first['job_id'],'completed',0)
        second=self.accepted()
        rows=self.listing(query='?limit=1&offset=0').json()
        self.assertEqual(rows['total'],2);self.assertEqual(len(rows['documents']),1)
        self.assertEqual(rows['documents'][0]['document_id'],second['document_id'])
        row=self.listing(query='?limit=1&offset=1').json()['documents'][0]
        self.assertEqual(row['status'],'completed');self.assertEqual(row['document_id'],first['document_id'])
        self.assertEqual(self.listing(query='?offset=20').json()['documents'],[])
        store.transition(second['job_id'],'claim',0);store.transition(second['job_id'],'failed',0)
        self.assertEqual(self.listing().json()['documents'][0]['status'],'failed')

    def test_invalid_pagination(self):
        for query in ('?limit=0','?limit=51','?limit=x','?offset=-1'):
            self.assertEqual(self.listing(query=query).status_code,422)
        self.factory.assert_not_called()

    def test_authorized_file_headers_employee_and_unicode(self):
        item=self.accepted(filename='Policy Türkçe.pdf')
        response=self.http.get('/api/v1/documents/'+item['document_id']+'/file',headers=self.headers(role='employee'))
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.content,self.pdf)
        self.assertEqual(response.headers['content-type'],'application/pdf')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        self.assertTrue(response.headers['content-disposition'].startswith('attachment;'))
        self.assertNotIn('\n',response.headers['content-disposition'])
        self.assertNotIn(str(self.root),str(response.headers))

    def test_foreign_missing_and_malformed_are_same_404_before_open(self):
        item=self.accepted()
        with patch.object(self.service.storage,'read',side_effect=AssertionError('Must not open')):
            foreign=self.file(item['document_id'],'globex')
            missing=self.file('doc-'+str(uuid4()))
            malformed=self.file('invalid')
            self.assertEqual([r.status_code for r in (foreign,missing,malformed)],[404]*3)
            self.assertEqual(foreign.json(),missing.json());self.assertEqual(missing.json(),malformed.json())

    def test_missing_tampered_file(self):
        item=self.accepted();path=self.root/'acme'/item['document_id']/'policy.pdf'
        path.chmod(0o600);path.write_bytes(b'changed')
        self.assertEqual(self.file(item['document_id']).status_code,404)
        path.unlink();self.assertEqual(self.file(item['document_id']).status_code,404)

    def test_file_and_directory_symlinks(self):
        item=self.accepted();directory=self.root/'acme'/item['document_id'];path=directory/'policy.pdf'
        outside=self.root/'outside.pdf';outside.write_bytes(self.pdf)
        path.unlink();path.symlink_to(outside)
        self.assertEqual(self.file(item['document_id']).status_code,404)
        path.unlink();directory.rmdir();directory.symlink_to(self.root,target_is_directory=True)
        self.assertEqual(self.file(item['document_id']).status_code,404)
        self.assertEqual(outside.read_bytes(),self.pdf)

    def test_metadata_path_traversal_and_identity_conflict_fail_closed(self):
        item=self.accepted();key=PREFIX+'document:'+item['document_id'];original=json.loads(self.redis.get(key))
        for change in ({'filename':'../outside.pdf'},{'document_id':'doc-'+str(uuid4())},{'created_at':'bad'}):
            self.redis.set(key,json.dumps({**original,**change}))
            with patch.object(self.service.storage,'read',side_effect=AssertionError('Must not open')):
                response=self.file(item['document_id']);self.assertEqual(response.status_code,503)
                self.assertNotIn(str(self.root),response.text)

    def test_redis_failure_and_capacity_sanitized(self):
        self.factory.side_effect=ConnectionError('PRIVATE')
        for response in (self.listing(),self.file('doc-'+str(uuid4()))):
            self.assertEqual(response.status_code,503);self.assertNotIn('PRIVATE',response.text)
        self.factory.reset_mock()
        for _ in range(4):self.service.job_gate.acquire()
        try:
            self.assertEqual(self.listing().status_code,503)
            self.assertEqual(self.file('doc-'+str(uuid4())).status_code,503)
            self.factory.assert_not_called()
        finally:
            for _ in range(4):self.service.job_gate.release()

    def test_foreign_record_not_returned_or_opened(self):
        item=self.accepted('globex');key=PREFIX+'document:'+item['document_id']
        raw=json.loads(self.redis.get(key));raw['filename']='../private';self.redis.set(key,json.dumps(raw))
        self.assertEqual(self.listing().json()['total'],0)
        self.assertEqual(self.file(item['document_id']).status_code,404)

    def test_scan_bound_fails_closed(self):
        with patch('src.demo.upload_store.UploadStore.from_demo_env'):
            store=UploadStore(self.redis)
            with patch.object(self.redis,'scan',return_value=(1,[])),patch.object(self.service,'store_factory',return_value=store):
                self.assertEqual(self.listing().status_code,503)

    def test_normal_api_does_not_expose_demo_documents(self):
        from src.api.main import create_app
        paths={route.path for route in create_app().routes}
        self.assertNotIn('/api/v1/documents',paths)
        self.assertNotIn('/api/v1/documents/{document_id}/file',paths)
