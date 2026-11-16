import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.dependencies import authenticated_user, get_pipeline
from src.api.schemas import QueryRequest, QueryResponse
from src.api.resource_limits import QueryAdmission, QueryBodyLimit, QueryDeadline, QueryLimits
from src.auth.models import UserContext
from src.rag.execution import (
    api_query_execution, check_deadline, ProviderTimeout, ProviderUnavailable, QueryDeadlineExceeded,
)
from src.observability.tracing import configure_tracing


async def check_ready():
    from src.vector_store.qdrant_store import COLLECTION_NAME, client

    # Redis is a best-effort cache, so it does not gate readiness.
    if not await asyncio.wait_for(client.collection_exists(COLLECTION_NAME), timeout=3):
        raise RuntimeError("Collection unavailable")


@asynccontextmanager
async def lifespan(app):
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY must be configured")
    get_pipeline()  # Load clients and the reranker before accepting queries.
    configure_tracing()
    yield


def create_app(*, limits=None, admission_clock=None):
    limits = limits if limits is not None else QueryLimits.from_env()
    app = FastAPI(title="Enterprise RAG API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(QueryBodyLimit, limits=limits)
    app.add_middleware(QueryDeadline, limits=limits)
    admission = QueryAdmission(limits, clock=admission_clock)
    app.state.query_admission = admission

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors can contain the original input and validator context.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.post("/api/v1/query", response_model=QueryResponse)
    async def query(
        body: QueryRequest,
        user: UserContext = Depends(authenticated_user),
        pipeline=Depends(get_pipeline),
    ):
        check_deadline()
        admission.acquire(user.tenant_id)
        execution_token = api_query_execution.set(True)
        try:
            result = await pipeline(
                **body.model_dump(),
                model=os.getenv("GENERATION_MODEL", "gpt-4.1-mini"),
                user_context=user,
            )
            result = QueryResponse.model_validate(result)
            check_deadline()
            return result
        except QueryDeadlineExceeded:
            raise HTTPException(504, "Query deadline exceeded") from None
        except ProviderTimeout:
            raise HTTPException(504, "Upstream request timed out") from None
        except ProviderUnavailable:
            raise HTTPException(503, "Generation service temporarily unavailable") from None
        except Exception:
            raise HTTPException(500, "Query processing failed") from None
        finally:
            api_query_execution.reset(execution_token)
            admission.release()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        try:
            await check_ready()
        except Exception:
            raise HTTPException(503, "Service not ready") from None
        return {"status": "ready"}

    return app


app = create_app()
