# Query API

[Documentation index](README.md) · [Overview](../README.md) · [Architecture](architecture.md) · [Deployment](deployment.md) · [Testing](testing.md)

The FastAPI adapter exposes the existing async RAG pipeline. Local development
uses `http://127.0.0.1:8001`; the separate Compose project uses port `18001` in the
verified environment. Port selection is a Uvicorn/Compose setting, not hardcoded
in the application. See [startup instructions](deployment.md).

## Endpoints

| Method/path | Authentication | Response |
| --- | --- | --- |
| `POST /api/v1/query` | JWT Bearer | Grounded answer and cited source metadata |
| `GET /health` | Public | 200 `{"status":"ok"}`; no dependency calls |
| `GET /ready` | Public | 200 `{"status":"ready"}` or generic 503 |
| `GET /docs` | Public | Swagger UI with Bearer authorization |
| `GET /openapi.json` | Public | OpenAPI schema |

Readiness checks that the configured Qdrant collection exists, with a three-second
deadline. Redis is optional caching and does not gate readiness. A ready service
does not prove provider credential validity/quota, tenant corpus contents, or
end-to-end answer quality. An empty initialized collection can pass readiness.

Demo-only login is documented in [the isolated demo guide](demo.md#demo-session-api);
it is not exposed by this normal API entrypoint.

## Authentication and tenant identity

Send `Authorization: Bearer <token>`. The existing JWT utility verifies HS256
signature and required `sub`, `tenant_id`, `role`, `iat`, and `exp` claims, then
constructs typed `UserContext`. Invalid/expired tokens, missing required claims,
invalid roles, and blank user/tenant IDs are rejected. Supported roles are
`employee` and `admin`; both can query.

The signing secret comes from `JWT_SECRET_KEY`; a missing secret fails closed.
A trusted administrator must map users to tenants before issuing short-lived
tokens. There is no HTTP login, token issuance, provisioning, revocation, or key
rotation endpoint. The existing [JWT helper](../src/auth/jwt_handler.py) is for
trusted application/development use; do not print tokens into shared logs or
commit them. Store environment credentials outside tracked files.

Body `tenant_id`, `model`, and other unknown fields are rejected. Headers such as
`X-Tenant-ID` and URL parameters cannot change the verified tenant. The server
selects `GENERATION_MODEL` (default `gpt-4.1-mini`). Direct Python RAG calls remain
a trusted internal interface; see [tenant boundaries](architecture.md#tenant-isolation-and-caching).

## Query request

```json
{
  "query": "What is the remote work allowance?",
  "retrieval_limit": 5,
  "final_limit": 3,
  "lambda_value": 0.5,
  "use_cache": true
}
```

| Field | Validation/default |
| --- | --- |
| `query` | Required strict string, 1–2000 characters, nonblank |
| `retrieval_limit` | Strict integer, 1–10, default 5 |
| `final_limit` | Strict integer, 1–3 and <= retrieval limit, default 3 |
| `lambda_value` | Numeric value 0–1, default 0.5 |
| `use_cache` | Strict boolean, default true |

Only `query` is required. `use_cache=false` bypasses the answer cache, not the
embedding cache. Query requests can incur OpenAI charges; ordinary API operation
has no hard pre-call spending guard.

## Request resource protection

Before FastAPI parses query JSON, a pure-ASGI middleware limits actual received
body bytes to 32,768 and total body receive time to 5 seconds. A large declared
Content-Length is rejected early; missing/chunked/underdeclared lengths still use
actual byte counting. A disconnect during body receive starts no query work.
Malformed Content-Length returns generic 400. These checks also cover the
trailing-slash query path.

After authentication and schema validation, at most two queries may be active per
application process. Excess queries immediately receive 503 and `Retry-After: 1`;
there is no admission waiting queue. Health/readiness bypass these slots. Cache
and reranker operations run in threads for API queries so they do not block the
event loop. Reranker model calls are serialized. Cancellation retains the slot
until any active blocking operation finishes; it does not kill Python threads.
Blocking execution outside the API keeps its existing behavior; ingestion retains
its original embedding client/retry policy.

Defaults are configurable at app startup through `API_MAX_BODY_BYTES`,
`API_BODY_TIMEOUT_SECONDS`, `API_MAX_ACTIVE_QUERIES`, and
`API_QUERY_DEADLINE_SECONDS` (default 60); invalid values fail startup.
Compose forwards these to the API service. Query length and retrieval/final maxima
are schema limits. The absolute query deadline starts at application receipt,
including body receive, authentication/validation, admission, cache, retrieval,
reranking, both citation attempts and response validation. Body timeout remains
408 when its separate 5-second allowance expires first; query deadline is 504.

Each query OpenAI call has a 20-second allowance, shortened to the remaining query
deadline, with connect timeout at most 5 seconds. SDK retries and automatic redirects
are disabled for query clients. Generation has `max_output_tokens=500` and at most
two citation-validation attempts sharing the original deadline. A request hook
checks expiry again after SDK preparation, before HTTP transport. No new provider
call starts after expiry, and no fallback call is added. Ingestion batch embeddings
retain their original client, 20-second SDK timeout and one SDK retry.

Admission uses a rolling 60-second window: at most 6 accepted queries per verified
tenant and 12 globally, plus 50 accepted queries over the process lifetime. The
single critical section checks deadline, then capacity, lifetime quota, global
rate, and tenant rate, in that order. It reserves the active slot and increments
all applicable counters only when every check passes. Rejected requests start no
cache/retrieval/provider work and consume no allowance. Accepted failures, timeouts,
cancellations and cache HITs are never refunded; releasing an active slot does not
change rate history or lifetime usage. Health/readiness are excluded.

Rate history retains timestamps strictly newer than `now - window`; entries expire
at the exact window boundary. Rate rejections include a rounded-up `Retry-After`
in seconds. Lifetime quota rejections have no reset-time header. Expired tenant
history is removed, and rejected new tenants allocate no history.

`API_TENANT_RATE_LIMIT=6`, `API_GLOBAL_RATE_LIMIT=12`,
`API_RATE_WINDOW_SECONDS=60`, and `API_DEMO_MAX_QUERIES=50` configure these defaults;
all must be positive and finite (counts are integers). Settings are read at startup
and Compose forwards them only to the API. State is in memory for the single-app
process deployment: restart/recreating the app resets it, and multiple workers or
replicas have independent allowances. This is not a persistent/distributed tenant
quota or monetary billing cap. Deadlines and counters do not guarantee that remote
processing or billing stops.

A stuck operation can retain a slot indefinitely. Thread cancellation is not a
physical execution cutoff: HTTP 504 waits for existing blocking work to finish,
so it can arrive after the configured deadline. A success response cannot start
after expiry. Once headers have started, a stalled response delivery cannot be
replaced with a second HTTP status. Client disconnect after body
receive does not itself guarantee immediate query cancellation. Limits are local
to one app process, multiply across workers/replicas, and do not limit unauthenticated
connections or aggregate body buffers across connections. Use ingress protection
for a public deployment; this is not a latency or account-wide budget guarantee.

## Query response

Illustrative ACME response; generated wording varies:

```json
{
  "answer": "The annual remote work allowance is 500 USD [1].",
  "citations": [
    {
      "citation_id": 1,
      "sources": [
        {
          "document_id": "remote-work-policy",
          "filename": "acme-policy.pdf",
          "page": 1,
          "chunk_id": "chunk-001"
        }
      ]
    }
  ]
}
```

Typed responses expose answer and citation groups only; extra internal source
metadata is excluded. Token usage, cache status, and cost are not response fields;
[tracing](architecture.md#observability-and-cost) provides pipeline accounting.

## Error contract

| Status | Meaning |
| --- | --- |
| 401 | Invalid/missing authentication; includes `WWW-Authenticate: Bearer` |
| 408 | Query body receive timeout; `{"detail":"Request body timeout"}` |
| 413 | Query body too large; `{"detail":"Request body too large"}` |
| 429 | `Tenant query rate limit exceeded`, `Global query rate limit exceeded`, or `Demo query quota exhausted` in the `detail` field |
| 422 | Invalid body/schema; `{"detail":"Invalid request"}` without echoed input |
| 500 | Pipeline or response validation failure; `{"detail":"Query processing failed"}` |
| 503 | Query capacity exhausted, provider rate/availability failure (`{"detail":"Generation service temporarily unavailable"}`), or readiness failure; `{"detail":"Service not ready"}` |
| 504 | `{"detail":"Query deadline exceeded"}` or `{"detail":"Upstream request timed out"}` |

Central exception handling also returns generic 500 for unexpected application
errors. Upstream provider errors are not passed through as request/response bodies.
A provider authentication problem is not an end-user JWT 401. Empty retrieval,
invalid stored citation metadata, and exhausted citation retries currently follow
the generic query-500 contract. The isolated smoke runner records [sanitized failure categories](testing.md#safe-diagnostics-and-earlier-failures)
without changing production HTTP behavior.

## Swagger and Postman checks

1. Start the app/services using the [deployment guide](deployment.md). Check health
   and readiness without stopping existing services.
2. Open [local Swagger](http://127.0.0.1:8001/docs), or the separate Compose
   [Swagger instance](http://127.0.0.1:18001/docs). Use **Authorize** with a trusted,
   short-lived token; Postman uses its Bearer Token authorization setting.
3. After explicitly approving provider spend, submit the query body above for
   ACME and GLOBEX. Check the expected allowance and each citation's document ID,
   filename, page, and chunk. Repeat a cache-enabled request to inspect tracing.
4. Confirm missing/invalid/expired tokens give 401. A body tenant ID, blank query,
   wrong type, or final limit above retrieval limit should give 422. Tenant
   headers/URL parameters must not override the token tenant.

Use [deterministic tests](testing.md#deterministic-integration-tests) for no-spend checks.
The existing single-secret JWT trust model lacks issuer/audience policy, revocation,
persistent tenant quotas and production TLS/proxy configuration.
Request body, admission and query/provider time limits are described above. These are deployment/application limits, not guarantees added by
schema validation or the smoke test.

## Implementation references

[Application/lifecycle/errors](../src/api/main.py),
[authentication dependency](../src/api/dependencies.py),
[schemas](../src/api/schemas.py), and [RAG orchestration](../src/rag/pipeline.py).

## Demo route overview

All query/document identity comes from JWT claims, never a browser tenant field.
The demo frontend proxies `/api/v1/`, `/health` and `/ready` to the private API.
Swagger is accessed directly through the normal API configuration, not this proxy.

| Route | Scope and purpose |
| --- | --- |
| `POST /api/v1/demo/session` | Demo only; fixed account/password login, short-lived JWT |
| `POST /api/v1/documents` | Demo only; authenticated admin, one PDF and UUID `Idempotency-Key` |
| `GET /api/v1/ingestion-jobs/{job_id}` | Demo only; authenticated owner, sanitized job status |
| `GET /api/v1/documents?limit=20&offset=0` | Demo only; tenant library, limit 1–50 |
| `GET /api/v1/documents/{document_id}/file` | Demo only; authorized PDF attachment |
| `POST /api/v1/query` | Normal backend and demo; answer plus structured citations |
| `GET /health` | Process liveness; demo adds provider-mode metadata |
| `GET /ready` | Configured Qdrant collection availability, not provider readiness |
| `GET /docs` | FastAPI Swagger UI on the normal API's selected port |

[Query schemas](#query-request) · [HTTP errors](#error-contract) · [Session contract](demo.md#demo-session-api) ·
[Upload/job/document contracts](demo-uploads.md)
