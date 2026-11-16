"""Test-only fail-closed ledger at the serialized OpenAI HTTP boundary."""
import json
from decimal import Decimal

import httpx2 as httpx

GENERATION_MODEL = "gpt-4.1-mini"
EMBEDDING_MODEL = "text-embedding-3-small"
MAX_OUTPUT = 500
MAX_INPUT = 4096
EMBEDDING_INPUT_BOUND = 8192
BUDGET = Decimal("0.10")
# Two queries: (one single + one <=5-text batch) + <=2 generations apiece.
WORST_RESERVATION = Decimal("0.01171968")


class GuardStop(RuntimeError):
    def __init__(self):
        super().__init__("Smoke guard stopped execution; inspect numeric summary only")


def integer(value, *, maximum=None, minimum=0):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise GuardStop()
    return value


class Ledger:
    def __init__(self, budget=BUDGET):
        self.budget = min(Decimal(budget), BUDGET)
        self.reserved = Decimal(0)
        self.actual = Decimal(0)
        self.phase = 0
        self.finished = True
        self.aborted = False
        self.snapshot = None
        self.counts = {"embedding": 0, "generation": 0, "count": 0}
        self.local = {}
        self.events = []
        self.failure = None

    def stop(self):
        self.aborted = True
        raise GuardStop()

    def begin(self, index, tenant):
        if (self.aborted or not self.finished or index != self.phase + 1 or index > 3 or
                tenant != ("acme", "globex", "acme")[index - 1] or
                (index == 3 and self.snapshot is None)):
            self.stop()
        self.phase = index
        self.finished = False
        self.local = {"embedding": 0, "generation": 0, "count": 0, "single": 0, "batch": 0}

    def finish(self):
        if self.aborted or self.finished:
            self.stop()
        self.finished = True

    def permit(self, kind):
        if kind not in self.counts or self.aborted or self.finished or self.phase not in (1, 2):
            self.stop()
        if self.local[kind] >= 2 or self.counts[kind] >= 4:
            self.stop()

    def reserve(self, kind, tokens, output=0):
        self.permit(kind)
        integer(tokens, minimum=1)
        cost = (Decimal(tokens) * (Decimal("0.02") if kind == "embedding" else Decimal("0.40")) +
                Decimal(output) * Decimal("1.60")) / 1000000
        if self.reserved + cost > self.budget:
            self.stop()
        self.reserved += cost  # Never refund, including successful or ambiguous requests.
        self.local[kind] += 1
        self.counts[kind] += 1
        self.events.append({"kind": kind, "input_bound": tokens, "output_bound": output,
                            "reservation_usd": str(cost)})
        return cost

    def record_failure(self, exc, stage, status=None):
        if self.failure is None:
            from tests.live_smoke.diagnostics import classify
            self.failure = classify(exc, stage, status)

    def summary(self):
        return {"requests_started": self.phase, "provider_counts": dict(self.counts),
                "reserved_usd": str(self.reserved), "reported_cost_usd": str(self.actual),
                "aborted": self.aborted, "calls": [dict(event) for event in self.events],
                "failure": dict(self.failure) if self.failure is not None else None}


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, delegate, send_scope=None):
        self.ledger, self.delegate, self.send_scope = ledger, delegate, send_scope
        self.stage, self.status = "request_validation", None

    async def aclose(self):
        await self.delegate.aclose()

    async def send(self, path, body, original):
        self.status = None  # A preceding free count response is not this paid request's status.
        self.stage = "request_construction"
        headers = dict(original.headers)
        headers.pop("content-length", None)
        request = httpx.Request("POST", "https://api.openai.com/v1/" + path,
                                headers=headers, json=body, extensions=dict(original.extensions))
        self.stage = "transport"
        if self.send_scope is None:
            response = await self.delegate.handle_async_request(request)
        else:
            with self.send_scope():
                response = await self.delegate.handle_async_request(request)
        self.status = response.status_code
        try:
            self.stage = "response_read"
            await response.aread()
            self.stage = "provider_http"
            if response.status_code != 200:
                self.ledger.stop()  # Includes redirects, rate limits, timeouts/ambiguous errors.
            self.stage = "response_decode"
            data = response.json()
            self.stage = "response_validation"
            return response, data
        except BaseException:
            await response.aclose()
            raise

    async def handle_async_request(self, request):
        self.stage, self.status = "request_validation", None
        try:
            if (request.method != "POST" or request.url.scheme != "https" or
                    request.url.host != "api.openai.com" or request.url.port not in (None, 443) or
                    request.url.query):
                self.ledger.stop()
            self.stage = "request_decode"
            body = json.loads(await request.aread())
            self.stage = "request_validation"
            if request.url.path == "/v1/embeddings":
                return await self.embedding(body, request)
            if request.url.path == "/v1/responses":
                return await self.generation(body, request)
            self.ledger.stop()
        except BaseException as exc:
            self.ledger.aborted = True
            self.ledger.record_failure(exc, self.stage, self.status)
            raise GuardStop() from None

    async def embedding(self, body, request):
        self.ledger.permit("embedding")
        if (set(body) - {"model", "input", "encoding_format"} or
                body.get("model") != EMBEDDING_MODEL or
                body.get("encoding_format", "float") not in ("float", "base64")):
            self.ledger.stop()
        single = isinstance(body.get("input"), str)
        texts = [body["input"]] if single else body.get("input")
        shape = "single" if single else "batch"
        if (not isinstance(texts, list) or not 1 <= len(texts) <= 5 or
                any(not isinstance(t, str) or not t.strip() or len(t.encode()) > 2048 for t in texts) or
                self.ledger.local[shape] != 0 or self.ledger.local["generation"]):
            self.ledger.stop()
        bound = EMBEDDING_INPUT_BOUND * len(texts)
        self.ledger.reserve("embedding", bound)
        self.ledger.local[shape] += 1
        response, data = await self.send("embeddings", body, request)
        usage = data.get("usage", {})
        tokens = integer(usage.get("prompt_tokens"), maximum=bound, minimum=1)
        if integer(usage.get("total_tokens")) != tokens or data.get("model") != EMBEDDING_MODEL:
            self.ledger.stop()
        if len(data.get("data", [])) != len(texts):
            self.ledger.stop()
        self.ledger.actual += Decimal(tokens) * Decimal("0.02") / 1000000
        self.ledger.events[-1]["input_actual"] = tokens
        return response

    async def generation(self, body, request):
        self.ledger.permit("generation")
        allowed = {"model", "instructions", "input", "text", "stream", "max_output_tokens", "store", "truncation"}
        if (set(body) - allowed or body.get("model") != GENERATION_MODEL or body.get("stream", False) is not False or
                not isinstance(body.get("input"), str) or not isinstance(body.get("instructions"), str) or
                body.get("text", {}).get("format", {}).get("type") != "json_schema"):
            self.ledger.stop()
        body.update(max_output_tokens=MAX_OUTPUT, store=False, truncation="disabled", service_tier="default")
        count_body = {k: body[k] for k in ("model", "instructions", "input", "text", "truncation")}
        self.ledger.permit("count")
        self.ledger.local["count"] += 1
        self.ledger.counts["count"] += 1
        self.ledger.events.append({"kind": "count", "reservation_usd": "0"})
        count_response, count = await self.send("responses/input_tokens", count_body, request)
        await count_response.aclose()
        if count.get("object") != "response.input_tokens":
            self.ledger.stop()
        tokens = integer(count.get("input_tokens"), minimum=1, maximum=MAX_INPUT)
        self.ledger.reserve("generation", tokens, MAX_OUTPUT)
        response, data = await self.send("responses", body, request)
        usage = data.get("usage", {})
        if (data.get("status") != "completed" or data.get("service_tier") != "default" or
                data.get("model") not in (GENERATION_MODEL, "gpt-4.1-mini-2025-04-14") or
                integer(usage.get("input_tokens"), minimum=1) != tokens):
            self.ledger.stop()
        output = integer(usage.get("output_tokens"), maximum=MAX_OUTPUT)
        if integer(usage.get("total_tokens")) != tokens + output:
            self.ledger.stop()
        self.ledger.actual += (Decimal(tokens) * Decimal("0.40") + Decimal(output) * Decimal("1.60")) / 1000000
        self.ledger.events[-1].update(input_actual=tokens, output_actual=output)
        return response
