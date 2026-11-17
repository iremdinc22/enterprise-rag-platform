"""Explicit HTTP E2E for the isolated deterministic demo; synthetic uploads only.

No Docker lifecycle, provider credentials, signing secrets, or environment edits.
Creates exactly two documents; retained in demo storage for manual exploration.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

BASE_URL = 'http://127.0.0.1:18030'
ABSTENTION = "I don't have enough information in the provided sources to answer this question."


def synthetic_pdf(tenant, marker, amount):
    writer = PdfWriter()
    for text in (f'SYNTHETIC {tenant.upper()} demonstration reference sheet.',
                 f'SYNTHETIC {tenant.upper()} {marker} allowance is {amount} USD.'):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 30 750 Td ('+text+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    result = BytesIO(); writer.write(result)
    return result.getvalue()


def expected(response, status):
    if response.status_code != status:
        # Never emit raw headers, bodies, credentials, tokens or exception reprs.
        raise RuntimeError(f'Demo HTTP verification failed: expected {status}, received {response.status_code}')
    return response


def run(credentials):
    if set(credentials) != {'acme-demo','globex-demo'} or not all(isinstance(v,str) and v for v in credentials.values()):
        raise ValueError('Invalid demo credentials file')
    report = {'checks':[], 'documents':{}}
    def passed(name):report['checks'].append(name)
    with httpx.Client(base_url=BASE_URL, timeout=75, trust_env=False, follow_redirects=False) as client:
        health = expected(client.get('/health'),200)
        if health.headers.get('x-demo-provider-mode') != 'deterministic':
            raise RuntimeError('An explicitly enabled deterministic demo is required')
        passed('deterministic mode metadata')
        headers = {}
        for tenant in ('acme','globex'):
            account=tenant+'-demo'
            session=expected(client.post('/api/v1/demo/session',json={'username':account,'password':credentials[account]}),200).json()
            assert session['user']['tenant_id']==tenant and session['expires_in']==900
            headers[tenant]={'Authorization':'Bearer '+session['access_token']}
        passed('ACME/GLOBEX authenticated sessions')
        expected(client.post('/api/v1/query',json={'query':'Synthetic test'}),401)
        passed('unauthenticated query rejection')
        # Empty-corpus abstention uses actual BM25/vector/RAG, not an API shortcut.
        for tenant,marker in (('acme','Orion'),('globex','Nebula')):
            docs=expected(client.get('/api/v1/documents',headers=headers[tenant]),200).json()
            if docs['total']==0:
                result=expected(client.post('/api/v1/query',headers=headers[tenant],json={'query':f'What is the {marker} allowance?'}),200).json()
                assert result=={'answer':ABSTENTION,'citations':[]}
        passed('empty library safe no-evidence behavior where initially empty')
        submissions={}; files={}; keys={}
        for tenant,marker,amount in (('acme','Orion','731'),('globex','Nebula','284')):
            data=synthetic_pdf(tenant,marker,amount);files[tenant]=data;key=str(uuid4());keys[tenant]=key
            filename=f'SYNTHETIC-{tenant}-{marker.lower()}.pdf'
            response=expected(client.post('/api/v1/documents',headers={**headers[tenant],'Idempotency-Key':key},files={'file':(filename,data,'application/pdf')}),202)
            submissions[tenant]=response.json()
            assert response.json()['status'] in ('queued','processing','completed','submission_unknown')
        passed('two PDF uploads accepted with generated document/job IDs')
        for tenant,submission in submissions.items():
            for _ in range(45):
                status=expected(client.get('/api/v1/ingestion-jobs/'+submission['job_id'],headers=headers[tenant]),200).json()
                if status['status']=='completed':break
                if status['status']=='failed':raise RuntimeError('Synthetic demo ingestion failed')
                time.sleep(2)
            else:raise RuntimeError('Demo ingestion verification timed out')
        passed('both actual Celery jobs completed')
        for tenant,marker,amount in (('acme','Orion','731'),('globex','Nebula','284')):
            submission=submissions[tenant]
            docs=expected(client.get('/api/v1/documents',headers=headers[tenant]),200).json()['documents']
            assert any(d['document_id']==submission['document_id'] and d['status']=='completed' and d['page_count']==2 for d in docs)
            pdf=expected(client.get('/api/v1/documents/'+submission['document_id']+'/file',headers=headers[tenant]),200)
            assert hashlib.sha256(pdf.content).digest()==hashlib.sha256(files[tenant]).digest()
            result=expected(client.post('/api/v1/query',headers=headers[tenant],json={'query':f'What is the {marker} allowance?'}),200).json()
            assert amount+' USD' in result['answer'] and 'Simulated AI' in result['answer']
            assert result['citations'] and all(source['document_id']==submission['document_id'] and source['page']==2
                for group in result['citations'] for source in group['sources'])
            report['documents'][tenant]={'document_id':submission['document_id'],'job_id':submission['job_id'],
                'filename':f'SYNTHETIC-{tenant}-{marker.lower()}.pdf','page':2,'verified_amount_usd':amount}
        passed('tenant-owned listing and exact PDF downloads')
        passed('retrieved unique facts and real page-2 citations')
        # Identical replay must not enqueue another ingestion or index another doc.
        replay=expected(client.post('/api/v1/documents',headers={**headers['acme'],'Idempotency-Key':keys['acme']},files={'file':('SYNTHETIC-acme-orion.pdf',files['acme'],'application/pdf')}),202).json()
        assert replay['job_id']==submissions['acme']['job_id'] and replay['document_id']==submissions['acme']['document_id']
        expected(client.post('/api/v1/documents',headers={**headers['acme'],'Idempotency-Key':keys['acme']},files={'file':('different.pdf',files['globex'],'application/pdf')}),409)
        passed('idempotent replay and different-file conflict')
        malformed=expected(client.post('/api/v1/documents',headers={**headers['acme'],'Idempotency-Key':str(uuid4())},files={'file':('SYNTHETIC-invalid.pdf',b'not a PDF','application/pdf')}),422)
        assert str(Path.cwd()) not in malformed.text
        passed('malformed PDF sanitized rejection')
        for tenant,foreign,marker in (('acme','globex','Nebula'),('globex','acme','Orion')):
            for endpoint in ('/api/v1/ingestion-jobs/'+submissions[foreign]['job_id'], '/api/v1/documents/'+submissions[foreign]['document_id']+'/file'):
                response=expected(client.get(endpoint,headers=headers[tenant]),404)
                missing_endpoint=('/api/v1/ingestion-jobs/' if 'ingestion-jobs' in endpoint else '/api/v1/documents/doc-')+str(uuid4())+('' if 'ingestion-jobs' in endpoint else '/file')
                missing=expected(client.get(missing_endpoint,headers=headers[tenant]),404)
                assert response.json()==missing.json()
            result=expected(client.post('/api/v1/query',headers=headers[tenant],json={'query':f'What is the {marker} allowance?'}),200).json()
            assert result=={'answer':ABSTENTION,'citations':[]}
        passed('cross-tenant job/PDF denial and foreign-fact abstention')
        repeat=expected(client.post('/api/v1/query',headers=headers['acme'],json={'query':'What is the Orion allowance?'}),200).json()
        assert '731 USD' in repeat['answer'] and repeat['citations'][0]['sources'][0]['document_id']==submissions['acme']['document_id']
        passed('cache-enabled repeat retains valid tenant citations')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-demo-e2e',action='store_true')
    parser.add_argument('--credentials-file',required=True,type=Path)
    parser.add_argument('--report-file',required=True,type=Path)
    args=parser.parse_args()
    if not args.execute_demo_e2e:parser.error('Explicit synthetic demo execution flag required')
    try:
        report=run(json.loads(args.credentials_file.read_text()))
    except Exception as error:
        # SDK/network exception representations may contain payloads; emit class only.
        raise SystemExit('Demo E2E failed ('+type(error).__name__+'); no automatic rerun.') from None
    args.report_file.write_text(json.dumps(report,indent=2))
    print('PASS:',len(report['checks']),'isolated Docker/HTTP checks; two synthetic documents retained.')


if __name__=='__main__':main()
