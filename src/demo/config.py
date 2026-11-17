"""Explicit, offline validation; no dotenv loading or service connections."""
from dataclasses import dataclass, field
import os

ACTIVE_SETTINGS = None


@dataclass(frozen=True)
class DemoSettings:
    jwt_secret: str = field(repr=False)
    upload_root: str = "/demo/uploads"
    max_upload_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 10
    max_chunks: int = 200
    provider_mode: str = "disabled"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        required = {
            "DEMO_ENABLED": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "QDRANT_URL": "http://qdrant:6333",
            "QDRANT_COLLECTION": "enterprise_demo_documents",
            "CELERY_BROKER_URL": "redis://redis:6379/0",
            "REDIS_JOBS_URL": "redis://redis:6379/1",
            "REDIS_CACHE_URL": "redis://redis:6379/2",
            "DEMO_UPLOAD_ROOT": "/demo/uploads",
            "HF_HOME": "/home/app/.cache/huggingface",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "RAG_TRACING_ENABLED": "false",
        }
        if any(env.get(key) != value for key, value in required.items()):
            raise ValueError("Invalid demo configuration")
        mode = env.get("DEMO_PROVIDER_MODE")
        if mode not in ("disabled", "deterministic"):
            raise ValueError("Invalid demo configuration")
        # Never inherit credentials from the existing host/Compose environment.
        if env.get("JWT_SECRET_KEY") or env.get("OPENAI_API_KEY"):
            raise ValueError("Invalid demo configuration")
        secret = env.get("DEMO_JWT_SECRET", "")
        if (not isinstance(secret, str) or not 32 <= len(secret) <= 256
                or any(not 33 <= ord(ch) <= 126 for ch in secret)):
            raise ValueError("Invalid demo configuration")
        limits = []
        for key, maximum in (("DEMO_MAX_UPLOAD_BYTES", 5 * 1024 * 1024),
                             ("DEMO_MAX_PDF_PAGES", 10), ("DEMO_MAX_CHUNKS", 200)):
            value = env.get(key)
            if not isinstance(value, str) or not value.isascii() or not value.isdigit():
                raise ValueError("Invalid demo configuration")
            number = int(value)
            if not 1 <= number <= maximum:
                raise ValueError("Invalid demo configuration")
            limits.append(number)
        return cls(secret, max_upload_bytes=limits[0], max_pdf_pages=limits[1], max_chunks=limits[2], provider_mode=mode)
