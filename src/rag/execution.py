"""Offload API query blocking stages without abandoning work on cancellation."""
import asyncio
from contextvars import ContextVar

api_query_execution = ContextVar("api_query_execution", default=False)
PROVIDER_REQUEST_TIMEOUT = 20.0
PROVIDER_CONNECT_TIMEOUT = 5.0


async def run_blocking(function, *args, **kwargs):
    check_deadline()
    # Direct Python/ingestion callers keep their existing execution behavior.
    if not api_query_execution.get():
        return function(*args, **kwargs)
    def execute():
        check_deadline()
        return function(*args, **kwargs)
    task = asyncio.create_task(asyncio.to_thread(execute))
    try:
        result = await asyncio.shield(task)
        check_deadline()
        return result
    except asyncio.CancelledError:
        # A Python thread cannot be killed. Keep the query/admission slot alive
        # until it really finishes, even if the caller cancels repeatedly.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Consume any worker failure; cancellation still wins.
        raise


query_deadline = ContextVar("query_deadline", default=None)
_provider_deadline = ContextVar("provider_deadline", default=None)


class QueryDeadlineExceeded(RuntimeError):
    pass


class ProviderTimeout(RuntimeError):
    pass


class ProviderUnavailable(RuntimeError):
    pass


def check_deadline():
    import time
    deadline = query_deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise QueryDeadlineExceeded("Query deadline exceeded")


async def _before_provider_send(request):
    """Check again after SDK serialization, immediately before HTTP transport."""
    import time
    check_deadline()
    deadlines = [d for d in (query_deadline.get(), _provider_deadline.get()) if d is not None]
    remaining = min(PROVIDER_REQUEST_TIMEOUT, min(deadlines) - time.monotonic()) if deadlines else PROVIDER_REQUEST_TIMEOUT
    if remaining <= 0:
        raise ProviderTimeout("Upstream request timed out")
    request.extensions["timeout"] = {
        "connect": min(PROVIDER_CONNECT_TIMEOUT, remaining), "read": remaining,
        "write": remaining, "pool": remaining,
    }


def query_http_client(*, transport=None):
    from openai import DefaultAsyncHttpxClient
    return DefaultAsyncHttpxClient(
        transport=transport, event_hooks={"request": [_before_provider_send]}, follow_redirects=False,
    )


async def provider_request(call, **kwargs):
    """One query provider attempt, with no fallback/retry or billing assumptions."""
    import time
    from openai import APIConnectionError, APIStatusError, APITimeoutError, Timeout

    check_deadline()
    deadline = query_deadline.get()
    timeout = min(PROVIDER_REQUEST_TIMEOUT, deadline - time.monotonic()) if deadline is not None else PROVIDER_REQUEST_TIMEOUT
    if timeout <= 0:
        raise QueryDeadlineExceeded("Query deadline exceeded")
    started = time.monotonic()
    token = _provider_deadline.set(started + timeout)
    try:
        async with asyncio.timeout(timeout):
            result = await call(**kwargs, timeout=Timeout(timeout, connect=min(PROVIDER_CONNECT_TIMEOUT, timeout)))
        check_deadline()
        if time.monotonic() - started >= timeout:
            raise ProviderTimeout("Upstream request timed out")
        return result
    except (APITimeoutError, TimeoutError):
        check_deadline()
        raise ProviderTimeout("Upstream request timed out") from None
    except (APIConnectionError, APIStatusError):
        check_deadline()
        if time.monotonic() - started >= timeout:
            raise ProviderTimeout("Upstream request timed out") from None
        raise ProviderUnavailable("Generation service temporarily unavailable") from None
    finally:
        _provider_deadline.reset(token)
