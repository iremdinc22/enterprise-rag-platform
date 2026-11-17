"""Existing API contracts with an isolated disabled/deterministic demo lifecycle."""
from contextlib import asynccontextmanager
import sys
import os


def build_app(*, login_clock=None, upload_service=None):
    from src.demo.config import ACTIVE_SETTINGS
    if ACTIVE_SETTINGS is None or os.getenv("DEMO_ENABLED") != "1":
        raise ValueError("Demo runtime must be activated before API imports")
    from fastapi import HTTPException
    from src.api.main import create_app
    from src.api.dependencies import get_pipeline

    app = create_app()
    from src.demo.sessions import install_sessions
    install_sessions(app, clock=login_clock)
    from src.demo.uploads import install_uploads
    install_uploads(app, ACTIVE_SETTINGS, service=upload_service)
    from src.demo.documents import install_documents
    install_documents(app, app.state.upload_service)

    def unavailable():
        # Dependency rejection occurs before admission, retrieval or cache access.
        raise HTTPException(503, "Demo provider is disabled")

    if ACTIVE_SETTINGS.provider_mode == "disabled":
        app.dependency_overrides[get_pipeline] = unavailable
    app.add_middleware(DemoModeHeader, mode=ACTIVE_SETTINGS.provider_mode)

    @asynccontextmanager
    async def lifespan(app):
        try:
            # Fail closed, without migrating or recreating the isolated collection.
            if ACTIVE_SETTINGS.provider_mode == "deterministic":
                from src.demo.deterministic import DIMENSIONS
                from src.vector_store.qdrant_store import get_collection_dimension
                if await get_collection_dimension() != DIMENSIONS:
                    raise RuntimeError("Demo collection dimensions incompatible")
                get_pipeline()
            yield
        finally:
            store = sys.modules.get("src.vector_store.qdrant_store")
            if store is not None:
                await store.client.close()

    app.router.lifespan_context = lifespan
    return app


class DemoModeHeader:
    """Same-origin health metadata; no query/session response schema changes."""
    def __init__(self, app, *, mode):
        self.app, self.mode = app, mode

    async def __call__(self, scope, receive, send):
        async def metadata(message):
            if message["type"] == "http.response.start" and scope.get("path") == "/health":
                message = dict(message)
                message["headers"] = [*message.get("headers", []),
                    (b"x-demo-provider-mode", self.mode.encode("ascii"))]
            await send(message)
        return await self.app(scope, receive, metadata)
