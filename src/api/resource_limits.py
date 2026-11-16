"""Query body limits before FastAPI parsing, and immediate local admission."""
import asyncio
from collections import deque
from dataclasses import dataclass
import math
import os
import time
from threading import Lock

from starlette.responses import JSONResponse
from fastapi import HTTPException
from src.rag.execution import QueryDeadlineExceeded, check_deadline, query_deadline


@dataclass(frozen=True)
class QueryLimits:
    max_body_bytes: int = 32768
    body_timeout_seconds: float = 5.0
    max_active_queries: int = 2
    query_deadline_seconds: float = 60.0
    tenant_rate_limit: int = 6
    global_rate_limit: int = 12
    rate_window_seconds: float = 60.0
    demo_max_queries: int = 50

    def __post_init__(self):
        if (type(self.max_body_bytes) is not int or self.max_body_bytes <= 0
                or type(self.max_active_queries) is not int or self.max_active_queries <= 0
                or isinstance(self.body_timeout_seconds, bool)
                or not math.isfinite(self.body_timeout_seconds) or self.body_timeout_seconds <= 0
                or isinstance(self.query_deadline_seconds, bool)
                or not math.isfinite(self.query_deadline_seconds) or self.query_deadline_seconds <= 0):
            raise ValueError("Invalid API resource limits")
        if (any(type(value) is not int or value <= 0 for value in (
                self.tenant_rate_limit, self.global_rate_limit, self.demo_max_queries))
                or isinstance(self.rate_window_seconds, bool)
                or not math.isfinite(self.rate_window_seconds) or self.rate_window_seconds <= 0):
            raise ValueError("Invalid API resource limits")

    @classmethod
    def from_env(cls):
        try:
            return cls(
                max_body_bytes=int(os.getenv("API_MAX_BODY_BYTES", "32768")),
                body_timeout_seconds=float(os.getenv("API_BODY_TIMEOUT_SECONDS", "5")),
                max_active_queries=int(os.getenv("API_MAX_ACTIVE_QUERIES", "2")),
                query_deadline_seconds=float(os.getenv("API_QUERY_DEADLINE_SECONDS", "60")),
                tenant_rate_limit=int(os.getenv("API_TENANT_RATE_LIMIT", "6")),
                global_rate_limit=int(os.getenv("API_GLOBAL_RATE_LIMIT", "12")),
                rate_window_seconds=float(os.getenv("API_RATE_WINDOW_SECONDS", "60")),
                demo_max_queries=int(os.getenv("API_DEMO_MAX_QUERIES", "50")),
            )
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid API resource limits") from None


class QueryAdmission:
    """Atomic capacity/quota/rate decision; only fully admitted queries count."""
    def __init__(self, limits, *, clock=None):
        self.limit = limits.max_active_queries
        self.limits = limits
        self.clock = clock if clock is not None else time.monotonic
        self.active = 0
        self.accepted = 0
        self._global = deque()
        self._tenants = {}
        self._lock = Lock()

    def acquire(self, tenant_id):
        with self._lock:
            check_deadline()
            # Stable precedence: capacity, lifetime quota, global rate, tenant rate.
            if self.active >= self.limit:
                raise HTTPException(503, "Query capacity exhausted", headers={"Retry-After": "1"})
            if self.accepted >= self.limits.demo_max_queries:
                raise HTTPException(429, "Demo query quota exhausted")
            now = self.clock()
            cutoff = now - self.limits.rate_window_seconds
            while self._global and self._global[0][0] <= cutoff:
                _, tenant = self._global.popleft()
                self._tenants[tenant].popleft()
                if not self._tenants[tenant]:
                    del self._tenants[tenant]
            tenant_events = self._tenants.get(tenant_id)
            if len(self._global) >= self.limits.global_rate_limit:
                retry = max(1, math.ceil(self._global[0][0] + self.limits.rate_window_seconds - now))
                raise HTTPException(429, "Global query rate limit exceeded", headers={"Retry-After": str(retry)})
            if tenant_events and len(tenant_events) >= self.limits.tenant_rate_limit:
                retry = max(1, math.ceil(tenant_events[0] + self.limits.rate_window_seconds - now))
                raise HTTPException(429, "Tenant query rate limit exceeded", headers={"Retry-After": str(retry)})
            self.active += 1
            self.accepted += 1
            self._global.append((now, tenant_id))
            self._tenants.setdefault(tenant_id, deque()).append(now)

    def release(self):
        with self._lock:
            self.active -= 1


class QueryBodyLimit:
    """Buffer at most the configured bytes before invoking FastAPI's JSON parser."""
    def __init__(self, app, *, limits, path="/api/v1/query"):
        self.app, self.limits, self.path = app, limits, path

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") != self.path):
            return await self.app(scope, receive, send)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

        lengths = [v for k, v in scope["headers"] if k.lower() == b"content-length"]
        if lengths:
            # HTTP framing is also checked by the ASGI server. Never trust the
            # declared length as a substitute for counting actual received bytes.
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await reject(400, "Invalid request")
            declared = lengths[0].lstrip(b"0") or b"0"
            maximum = str(self.limits.max_body_bytes).encode("ascii")
            if len(declared) > len(maximum) or (len(declared) == len(maximum) and declared > maximum):
                return await reject(413, "Request body too large")
        body, size = bytearray(), 0
        failure = None
        try:
            async with asyncio.timeout(self.limits.body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return  # Client is gone; no parser, authentication or query work starts.
                    if message["type"] != "http.request":
                        failure = (400, "Invalid request")
                        break
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > self.limits.max_body_bytes:
                        failure = (413, "Request body too large")
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            failure = (408, "Request body timeout")
        if failure:
            return await reject(*failure)

        body = bytes(body)
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, buffered_receive, send)


class QueryDeadline:
    """Absolute application deadline, including body parsing and dependencies.

    Cancellation drains blocking stages: 504 can be delayed until such work ends.
    Response delivery after headers start cannot be replaced with another status.
    """
    def __init__(self, app, *, limits):
        self.app, self.limits = app, limits

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") != "/api/v1/query"):
            return await self.app(scope, receive, send)
        import time
        deadline = time.monotonic() + self.limits.query_deadline_seconds
        token = query_deadline.set(deadline)
        started = False

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                check_deadline()
                started = True
            await send(message)

        timer = asyncio.timeout_at(asyncio.get_running_loop().time() + self.limits.query_deadline_seconds)
        try:
            try:
                async with timer:
                    await self.app(scope, receive, guarded_send)
            except (TimeoutError, QueryDeadlineExceeded) as exc:
                if isinstance(exc, TimeoutError) and not timer.expired():
                    raise
                if started:
                    raise  # Never send a second response after headers were emitted.
                await JSONResponse({"detail": "Query deadline exceeded"}, status_code=504)(scope, receive, send)
        finally:
            query_deadline.reset(token)
