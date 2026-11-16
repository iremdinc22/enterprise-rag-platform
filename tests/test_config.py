"""Deployment configuration must preserve host defaults and Docker routing."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def config(self, values):
        with patch.dict(os.environ, values, clear=True), patch("dotenv.load_dotenv"):
            return runpy.run_path(str(ROOT / "src/config.py"))

    def test_existing_host_defaults(self):
        config = self.config({})
        self.assertEqual(config["QDRANT_URL"], "http://localhost:6333")
        self.assertEqual(config["QDRANT_COLLECTION"], "enterprise_documents")
        self.assertEqual(config["CELERY_BROKER_URL"], "redis://localhost:6381/0")
        self.assertEqual(config["REDIS_JOBS_URL"], "redis://localhost:6381/1")
        self.assertEqual(config["REDIS_CACHE_URL"], "redis://localhost:6381/2")
        self.assertEqual(config["EMBEDDING_MODEL"], "text-embedding-3-small")
        self.assertEqual(config["RERANKER_MODEL"], "cross-encoder/ms-marco-MiniLM-L6-v2")

    def test_environment_overrides(self):
        values = {
            "QDRANT_URL": "http://qdrant:6333", "QDRANT_COLLECTION": "isolated",
            "CELERY_BROKER_URL": "redis://redis:6379/0",
            "REDIS_JOBS_URL": "redis://redis:6379/1", "REDIS_CACHE_URL": "redis://redis:6379/2",
            "EMBEDDING_MODEL": "embedding-test", "RERANKER_MODEL": "reranker-test",
            "RERANKER_REVISION": "revision-test",
        }
        config = self.config(values)
        for name, value in values.items():
            self.assertEqual(config[name], value)

    def test_clients_use_docker_endpoints(self):
        script = '''
import json
from unittest.mock import patch
with patch("qdrant_client.AsyncQdrantClient") as qdrant:
    from src.vector_store import qdrant_store
from src.cache import result_cache, embedding_cache
from src.jobs import job_store
from src.workers.celery_app import celery_app
from src.ingestion.embedder import create_embedding, create_embeddings
print(json.dumps({
    "qdrant": qdrant.call_args.kwargs["url"],
    "collection": qdrant_store.COLLECTION_NAME,
    "redis": [[c.connection_pool.connection_kwargs["host"], c.connection_pool.connection_kwargs["port"], c.connection_pool.connection_kwargs["db"]] for c in [result_cache.redis_client, embedding_cache.redis_client, job_store.redis_client]],
    "broker": celery_app.conf.broker_url,
    "models": [create_embedding.__defaults__[0], create_embeddings.__defaults__[0]],
}))
'''
        values = {
            **os.environ, "QDRANT_URL": "http://qdrant:6333", "QDRANT_COLLECTION": "isolated",
            "REDIS_CACHE_URL": "redis://redis:6379/2", "REDIS_JOBS_URL": "redis://redis:6379/1",
            "CELERY_BROKER_URL": "redis://redis:6379/0", "OPENAI_API_KEY": "config-test-not-a-key",
            "EMBEDDING_MODEL": "embedding-test",
        }
        result = json.loads(subprocess.check_output([sys.executable, "-c", script], env=values, cwd=ROOT, text=True))
        self.assertEqual(result["qdrant"], "http://qdrant:6333")
        self.assertEqual(result["collection"], "isolated")
        self.assertEqual(result["redis"], [["redis", 6379, 2], ["redis", 6379, 2], ["redis", 6379, 1]])
        self.assertEqual(result["broker"], "redis://redis:6379/0")
        self.assertEqual(result["models"], ["embedding-test", "embedding-test"])
