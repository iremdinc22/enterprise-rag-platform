"""Demo-only sessions; fixed account mapping and process-local anonymous limits."""
from collections import deque
import math
from threading import Lock
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.responses import JSONResponse

from src.demo.passwords import DemoCredentials, verify_password

SESSION_PATH = "/api/v1/demo/session"


class SessionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)

    @field_validator("password")
    @classmethod
    def valid_password_encoding(cls, value):
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid request") from None
        return value


class SessionUser(BaseModel):
    user_id: str
    tenant_id: str
    role: str


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    user: SessionUser


class LoginAdmission:
    """10 POST attempts/rolling minute, and one active password hash; no queue."""
    def __init__(self, clock=None):
        self.clock = time.monotonic if clock is None else clock
        self.events = deque()
        self.busy = False
        self.lock = Lock()

    def attempt(self):
        with self.lock:
            now = self.clock()
            while self.events and self.events[0] <= now - 60:
                self.events.popleft()
            if len(self.events) >= 10:
                return max(1, math.ceil(self.events[0] + 60 - now))
            self.events.append(now)
            return None

    def acquire_hash(self):
        with self.lock:
            if self.busy:
                raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "1"})
            self.busy = True

    def release_hash(self):
        with self.lock:
            self.busy = False


class SessionProtection:
    """Rate check before body reception; no-store on success, redirects and errors."""
    def __init__(self, app, *, admission):
        self.app, self.admission = app, admission

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].rstrip("/") != SESSION_PATH:
            return await self.app(scope, receive, send)
        started = False

        async def no_store(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        if scope["method"] == "POST":
            retry = self.admission.attempt()
            if retry is not None:
                return await JSONResponse({"detail": "Too many login attempts"}, status_code=429,
                    headers={"Retry-After": str(retry)})(scope, receive, no_store)
        try:
            return await self.app(scope, receive, no_store)
        except Exception:
            if started:
                raise
            return await JSONResponse({"detail": "Internal server error"}, status_code=500)(scope, receive, no_store)


def install_sessions(app, *, clock=None):
    from src.api.resource_limits import QueryBodyLimit, QueryLimits
    from src.auth.jwt_handler import create_access_token
    from src.rag.execution import api_query_execution, run_blocking

    credentials = DemoCredentials.from_env()
    admission = LoginAdmission(clock)
    app.state.login_admission = admission
    app.add_middleware(QueryBodyLimit, limits=QueryLimits(max_body_bytes=4096, body_timeout_seconds=5), path=SESSION_PATH)
    app.add_middleware(SessionProtection, admission=admission)

    @app.post(SESSION_PATH, response_model=SessionResponse)
    async def session(body: SessionRequest, request: Request):
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        # No normalization, tenant/role input, user-supplied hash, or dynamic accounts.
        known = body.username in ("acme-demo", "globex-demo")
        encoded = credentials.globex_hash if body.username == "globex-demo" else credentials.acme_hash
        admission.acquire_hash()
        try:
            # Reuse cancellation-draining offload so a cancelled hash retains its slot.
            token = api_query_execution.set(True)
            try:
                valid = await run_blocking(verify_password, body.password.get_secret_value(), encoded)
            finally:
                api_query_execution.reset(token)
            if not known or not valid:
                raise HTTPException(401, "Invalid credentials")
            tenant = "acme" if body.username == "acme-demo" else "globex"
            access_token = create_access_token(body.username, tenant, "admin", expires_minutes=15)
            return SessionResponse(access_token=access_token, user=SessionUser(
                user_id=body.username, tenant_id=tenant, role="admin"))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Demo authentication temporarily unavailable") from None
        finally:
            admission.release_hash()
