"""Process configuration; defaults preserve the original host-based setup."""
import os

from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "enterprise_documents")
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "redis://localhost:6381/2")
REDIS_JOBS_URL = os.getenv("REDIS_JOBS_URL", "redis://localhost:6381/1")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6381/0")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2")
RERANKER_REVISION = os.getenv("RERANKER_REVISION") or None
