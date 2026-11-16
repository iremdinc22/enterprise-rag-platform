# Query API

[Overview](../README.md) · [Architecture](architecture.md) · [Deployment](deployment.md) · [Testing](testing.md)

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
| `query` | Required strict string, 1–8000 characters, nonblank |
| `retrieval_limit` | Strict integer, 1–100, default 5 |
| `final_limit` | Strict integer, 1–100 and <= retrieval limit, default 3 |
| `lambda_value` | Numeric value 0–1, default 0.5 |
| `use_cache` | Strict boolean, default true |

Only `query` is required. `use_cache=false` bypasses the answer cache, not the
embedding cache. Query requests can incur OpenAI charges; ordinary API operation
has no hard pre-call spending guard.

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
| 422 | Invalid body/schema; `{"detail":"Invalid request"}` without echoed input |
| 500 | Pipeline or response validation failure; `{"detail":"Query processing failed"}` |
| 503 | Readiness failure; `{"detail":"Service not ready"}` |

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
rate limits, body-byte limits, per-request deadlines, and production TLS/proxy
configuration. These are deployment/application limits, not guarantees added by
schema validation or the smoke test.

## Implementation references

[Application/lifecycle/errors](../src/api/main.py),
[authentication dependency](../src/api/dependencies.py),
[schemas](../src/api/schemas.py), and [RAG orchestration](../src/rag/pipeline.py).
