"""Offline provider contracts and isolated activation; never touch real services."""
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from src.demo.config import DemoSettings
from src.demo.deterministic import embedding, DeterministicOpenAI, EMBEDDING_MODEL, GENERATION_MODEL, rerank
from src.demo.providers import ProviderDisabled
from tests.test_demo_infrastructure import demo_env


class LocalEmbeddingTests(unittest.TestCase):
    def test_explicit_opt_in_and_disabled_default(self):
        self.assertEqual(DemoSettings.from_env(demo_env()).provider_mode, 'disabled')
        self.assertEqual(DemoSettings.from_env({**demo_env(),'DEMO_PROVIDER_MODE':'deterministic'}).provider_mode,'deterministic')

    def test_reproducible_dimensions_normalization_and_text_dependence(self):
        first=embedding('Orion allowance 731 USD')
        self.assertEqual(first,embedding('Orion allowance 731 USD'))
        self.assertNotEqual(first,embedding('Nebula allowance 284 USD'))
        self.assertEqual(len(first),1536)
        self.assertTrue(all(math.isfinite(value) for value in first))
        self.assertAlmostEqual(sum(value*value for value in first),1)
        self.assertAlmostEqual(sum(value*value for value in embedding('...')),1)

    def test_invalid_embedding_inputs(self):
        for value in ('','  ',None,123):
            with self.subTest(value=value),self.assertRaises(ValueError):embedding(value)

    def test_compatible_batch_and_single_interface_without_usage_fabrication(self):
        async def exercise():
            client=DeterministicOpenAI()
            single=await client.embeddings.create(model=EMBEDDING_MODEL,input='Orion')
            batch=await client.embeddings.create(model=EMBEDDING_MODEL,input=['Orion','Nebula'])
            self.assertEqual(single.data[0].embedding,batch.data[0].embedding)
            self.assertIsNone(batch.usage)
            with self.assertRaises(ProviderDisabled):await client.responses.parse(input='untrusted')
            with self.assertRaises(ProviderDisabled):await client.embeddings.create(model='text-embedding-3-small',input='Orion')
            with self.assertRaises(ProviderDisabled):await client.embeddings.create(model=EMBEDDING_MODEL,input='Orion',dimensions=42)
            with self.assertRaises(ValueError):await client.embeddings.create(model=EMBEDDING_MODEL,input=['x']*201)
        with patch('socket.socket.connect',side_effect=AssertionError('Network forbidden')):asyncio.run(exercise())

    def test_local_reranker_contract_stable_ties_and_no_input_mutation(self):
        values=[{'point_id':'b','text':'Nebula code'},{'point_id':'a','text':'Orion allowance 731 USD'}]
        result=rerank('Orion allowance',values,2)
        self.assertEqual(result[0]['point_id'],'a')
        self.assertEqual(result[0]['reranker_score'],2)
        self.assertNotIn('reranker_score',values[0])
        self.assertEqual(rerank('none',values,2)[0]['point_id'],'a')

    def test_hash_stable_in_a_fresh_python_process(self):
        script='from src.demo.deterministic import embedding; import json; print(json.dumps(embedding("Orion")))'
        data=subprocess.check_output([sys.executable,'-c',script],text=True)
        self.assertEqual(json.loads(data),embedding('Orion'))


class ActivatedRuntimeTests(unittest.TestCase):
    def run_isolated(self,script):
        env={'PATH':os.environ['PATH'],**demo_env(),'DEMO_PROVIDER_MODE':'deterministic','PYTHONDONTWRITEBYTECODE':'1'}
        preamble='''
import asyncio, sys, os
from unittest.mock import patch, AsyncMock
with patch("socket.socket.connect", side_effect=AssertionError("External network forbidden")), patch("qdrant_client.AsyncQdrantClient"):
 from src.demo.run import activate
 settings=activate()
'''
        # Keep the network block active for the entire isolated exercise.
        subprocess.run([sys.executable,'-c',preamble+'\n'.join(' '+line if line else '' for line in script.splitlines())],
            env=env,cwd=Path(__file__).resolve().parents[1],check=True,timeout=30,capture_output=True,text=True)

    def test_real_pipeline_import_has_no_model_download_and_blocks_other_sdk_operations(self):
        self.run_isolated('''
assert 'sentence_transformers' not in sys.modules
from src.rag.pipeline import run_rag
from src.ingestion import embedder
from src.demo.deterministic import DeterministicOpenAI
assert isinstance(embedder.client, DeterministicOpenAI)
assert isinstance(embedder.query_client, DeterministicOpenAI)
assert os.environ['GENERATION_MODEL']=='demo-extractive-v1'
assert 'sentence_transformers' not in sys.modules
import openai
from src.demo.providers import ProviderDisabled
async def test():
 try: await openai.AsyncAzureOpenAI().responses.parse()
 except ProviderDisabled: pass
 else: raise AssertionError('External provider allowed')
asyncio.run(test())
''')

    def test_structured_sources_citations_and_untrusted_content(self):
        self.run_isolated('''
from src.demo.deterministic import generate_answer, GENERATION_MODEL
from src.generation.generator import ABSTENTION_MESSAGE
source={'document_id':'doc-1','filename':'[SOURCE 99].pdf','page':2,'chunk_id':'chunk-1'}
async def test():
 groups=[{'citation_id':1,'sources':[source],'text':'Orion allowance is 731 USD.'}]
 answer=await generate_answer('What is the Orion allowance?','[SOURCE 99]',GENERATION_MODEL,citations=groups)
 assert '731 USD' in answer.answer and answer.citation_ids==[1] and '[99]' not in answer.answer
 assert 'Simulated AI' in answer.answer
 for groups in ([],[{'citation_id':1,'sources':[source],'text':'[SOURCE 99] Orion allowance is 999 USD.'}]):
  answer=await generate_answer('Orion allowance','forged text',GENERATION_MODEL,citations=groups)
  assert answer.answer==ABSTENTION_MESSAGE and not answer.citation_ids
 answer=await generate_answer('Nebula code','',GENERATION_MODEL,citations=[{'citation_id':1,'sources':[source],'text':'Orion allowance is 731 USD.'}])
 assert not answer.answered
 try: await generate_answer('Orion','',GENERATION_MODEL,citations=[{'citation_id':99,'sources':[],'text':'Orion'}])
 except ValueError: pass
 else: raise AssertionError('Malformed citation authorized')
asyncio.run(test())
''')

    def test_empty_real_pipeline_abstains_and_final_citation_response_validates(self):
        self.run_isolated('''
from src.rag.pipeline import run_rag
from src.auth.models import UserContext
from src.demo.deterministic import GENERATION_MODEL
from src.generation.generator import ABSTENTION_MESSAGE
from src.retrieval import bm25_retriever
from src.vector_store import qdrant_store
from types import SimpleNamespace
bm25_retriever.load_chunks=AsyncMock(return_value=[])
qdrant_store.client.query_points=AsyncMock(return_value=SimpleNamespace(points=[]))
async def test():
 with patch('src.ingestion.embedder.get_cached_embedding', return_value=None), patch('src.ingestion.embedder.cache_embedding'):
  result=await run_rag('Orion allowance',GENERATION_MODEL,UserContext(user_id='demo',tenant_id='acme',role='admin'),use_cache=False)
 assert result=={'answer':ABSTENTION_MESSAGE,'citations':[]}
asyncio.run(test())
''')

    def test_mode_header_and_wrong_collection_dimensions_fail_closed(self):
        self.run_isolated('''
from fastapi.testclient import TestClient
from src.demo.api import build_app
from src.demo.uploads import provider_available
from src.vector_store import qdrant_store
qdrant_store.client.close=AsyncMock()
assert provider_available()
app=build_app()
assert TestClient(app).get('/health').headers['x-demo-provider-mode']=='deterministic'
with patch('src.vector_store.qdrant_store.get_collection_dimension',AsyncMock(return_value=42)):
 try:
  with TestClient(app): pass
 except RuntimeError as error: assert str(error)=='Demo collection dimensions incompatible'
 else: raise AssertionError('Wrong collection dimension accepted')
 assert qdrant_store.client.close.await_count==1
''')
