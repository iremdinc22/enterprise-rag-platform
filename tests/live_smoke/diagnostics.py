"""Allowlisted error metadata only. Never serialize exceptions or frame locals."""
import socket
import ssl
import traceback

NAMES = {"GuardStop", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
         "RemoteProtocolError", "LocalProtocolError", "SSLError", "SSLCertVerificationError", "gaierror",
         "JSONDecodeError", "ValueError", "TypeError", "RuntimeError", "AssertionError", "TimeoutError",
         "CancelledError", "ExceptionGroup"}
FUNCTIONS = {"guarded_dns", "contextual_dns", "allowed", "guarded_connect", "guarded_ex", "host_text",
             "send", "handle_async_request", "embedding", "generation", "integer", "stop", "permit", "reserve"}
STAGES = {"request_validation", "request_decode", "request_construction", "transport", "response_read",
          "provider_http", "response_decode", "response_validation"}


def classify(exc, stage, status=None):
    name = type(exc).__name__
    name = name if name in NAMES else "UnknownException"
    stage = stage if stage in STAGES else "unknown"
    status = status if type(status) is int and 100 <= status <= 599 else None
    location = "tests/live_smoke/guard.py:handle_async_request"
    frames = traceback.extract_tb(exc.__traceback__)
    for frame in frames:
        filename = frame.filename.replace("\\", "/")
        if filename.endswith(("/tests/live_smoke/runtime.py", "/tests/live_smoke/guard.py")) and frame.name in FUNCTIONS:
            location = "tests/live_smoke/" + filename.rsplit("/", 1)[1] + ":" + frame.name + ":" + str(frame.lineno)
    chain = []; seen = set(); current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current)); chain.append(current)
        current = current.__cause__ or current.__context__
    if stage == "provider_http":
        category = {401: "provider_authentication", 403: "provider_permission", 429: "provider_rate_limit_or_quota"}.get(
            status, "provider_server_error" if status and status >= 500 else "provider_http_rejection")
    elif name == "GuardStop":
        category = "network_guard" if "/runtime.py:" in location else "guard_validation"
    elif any(isinstance(item, ssl.SSLError) for item in chain):
        category = "tls_failure"
    elif any(isinstance(item, socket.gaierror) for item in chain):
        category = "dns_failure"
    elif "Timeout" in name:
        category = "transport_timeout"
    elif stage in ("transport", "response_read"):
        category = "transport_failure"
    elif stage == "response_decode":
        category = "response_decode_failure"
    else:
        category = "unsupported_behavior"
    return {"exception_class": name, "failure_stage": stage, "http_status": status,
            "category": category, "source_location": location}
