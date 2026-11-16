"""Existing API contracts with an offline demo lifecycle and disabled queries."""
from contextlib import asynccontextmanager
import sys


def build_app():
    from src.demo.config import ACTIVE_SETTINGS
    if ACTIVE_SETTINGS is None:
        raise ValueError("Demo runtime must be activated before API imports")
    from fastapi import HTTPException
    from src.api.main import create_app
    from src.api.dependencies import get_pipeline

    app = create_app()

    def unavailable():
        # Dependency rejection occurs before admission, retrieval or cache access.
        raise HTTPException(503, "Demo provider is disabled")

    app.dependency_overrides[get_pipeline] = unavailable

    @asynccontextmanager
    async def lifespan(app):
        # No eager RAG/reranker load; /ready may lazily create the Qdrant client.
        try:
            yield
        finally:
            store = sys.modules.get("src.vector_store.qdrant_store")
            if store is not None:
                await store.client.close()

    app.router.lifespan_context = lifespan
    return app
