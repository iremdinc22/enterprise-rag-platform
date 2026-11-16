# Day 26: audit and authenticated query API

## Source audit and implementation plan

Audit baseline: core modules and experiment scripts; no FastAPI app, HTTP routes,
ASGI server dependency, or API tests. README claims were checked against source.

- `src/auth/jwt_handler.py`: HS256 signing/verification; requires `sub`, `tenant_id`,
  `role`, `iat`, `exp`; returns Pydantic `UserContext`. JWT secret is loaded from
  the environment / local `.env` at import; missing secret fails closed.
- `src/auth/models.py` and `authorization.py`: employee/admin roles; upload helper
  exists but is not called by ingestion itself. Both roles may query.
- `src/rag/pipeline.py`: async `run_rag(query, model, user_context, ...)`, returns
  answer/citations. It trusts callers to authenticate the supplied context.
- BM25 `load_chunks()` and Qdrant `search_chunks()` filter by tenant; result cache
  keys include tenant. API forwards the verified context without rewriting retrieval.
- `src/observability`: opt-in console tracing and ContextVar query usage collector;
  root span carries tokens/costs, including retries; cache hits incur zero new usage.
- `evaluation/tests`: unittest validation/metrics, mocked generation, and live
  tenant retrieval tests. Historical experiment scripts are not a unit test suite.
- Importing the pipeline initializes OpenAI clients and a Cross-Encoder. FastAPI
  and Uvicorn were missing; Pydantic v2 is needed for API schema validators.
- Synchronous Redis/reranking work can block the event loop. BM25 reads at most
  100 chunks and has no empty-corpus handling. Empty context and exhausted citation
  validation currently raise errors. Document replacement is non-atomic; cache
  invalidation is best effort. These behaviors remain as existing limitations.
- Existing citation diagnostics printed generated content, cache failure messages
  printed exception details, and spans captured raw exceptions. Those diagnostics
  are now generic; tracing retains span structure/metrics and a safe root error status.

No detailed Day 27–30 specification was found. Proposed plan, subject to approval:

| Day | Scope |
| --- | --- |
| 26 | FastAPI query adapter, JWT boundary, schemas, probes, safe errors, API tests |
| 27 | Admin-authorized ingestion/job endpoints using existing Celery interfaces |
| 28 | Timeouts, admission/concurrency controls, reliability and cache hardening |
| 29 | Deployment packaging, service configuration, operational telemetry |
| 30 | End-to-end isolation/load verification and operational documentation |

Only Day 26 is implemented.

## Architecture and contract

`src/api/main.py` exposes `app` and `create_app()`, owns lifecycle, probes, routes,
and exception handlers. `dependencies.py` reuses JWT verification and supplies the
existing pipeline. `schemas.py` defines explicit input/output contracts.

- `POST /api/v1/query`: requires HTTP Bearer JWT; accepts `query` (1–8000 characters,
  nonblank), `retrieval_limit` (1–100, default 5), `final_limit` (1–retrieval_limit,
  default 3), `lambda_value` (0–1, default 0.5), and `use_cache` (boolean, default true).
  Unknown fields, including `tenant_id` and `model`, are rejected with 422.
  Model comes from server `GENERATION_MODEL`, default `gpt-4.1-mini`.
  Returns `answer` and typed citation groups with document ID, filename, page,
  and chunk ID. Extra internal source metadata is not serialized.
- `/health`: public liveness, 200 `{"status":"ok"}`. No dependency calls.
- `/ready`: public readiness, checks the existing Qdrant collection with a three
  second deadline; 200 `{"status":"ready"}` or generic 503. Redis is optional
  because cache failures are designed to fail open. This does not verify OpenAI
  credentials/quota, model availability at the provider, or tenant corpus contents.
- `/docs` and `/openapi.json`: Swagger UI and schema with Bearer security.
- Missing/malformed/expired/bad-signature JWTs, missing required claims, invalid
  roles and empty user/tenant identities return generic 401 plus `WWW-Authenticate`.
  Invalid bodies return generic 422 without echoing submitted data. Pipeline and
  invalid pipeline-output failures return generic 500, without exception details.

Tenant identity comes exclusively from `decode_access_token()`. Request headers
and query parameters cannot override it. Body tenant IDs are rejected. There is no
login, tenant provisioning, token issuance endpoint, or client-selected model.
Trusted administration must associate users with tenants before issuing tokens.

The existing `run_rag()` is awaited directly. Its retrieval/generation, retry,
cache, usage reset and root span remain in place. Usage stays in tracing rather
than becoming a new response field. Raw exception recording is disabled for RAG
spans; the root still marks failures with a generic ERROR status. No authorization
headers, questions, generated answers or provider exceptions are logged by the API.
Application logs use static cache/validation messages. Separately configured third
party debug/HTTP logging should remain disabled. Start with access logging disabled
so query strings cannot become a second input logging channel.

## Start

From the repository root:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001 --no-access-log
```

Configure `JWT_SECRET_KEY`, `OPENAI_API_KEY`, optional `GENERATION_MODEL`, and
`RAG_TRACING_ENABLED` in the environment or local ignored `.env` (see README).
Never use the test secret outside tests. Startup loads the existing clients and
reranker before serving; first load can download weights. Qdrant must be running
on localhost:6333 with `enterprise_documents` indexed; Redis cache uses
localhost:6381 (container port 6379). In this verified environment, the existing
containers are `stupefied_dewdney` (`qdrant/qdrant`) and `enterprise-rag-redis`
(`redis:latest`). Container names are not used by the application: it connects
through the published host ports. Redis database 0 is the Celery broker, database
1 stores job states, and database 2 stores both caches. Port 6379 on the host belongs
to another Redis service; the platform correctly uses host port 6381.

Port 8000 is occupied by Portainer, so use **8001** for FastAPI here. The ASGI app
does not hardcode a listening port; Uvicorn's `--port` selects it.

The README Docker commands are fresh-install examples, not the names of this
existing environment. Do not run its `docker run` commands here or recreate the
collection. If these existing containers are stopped, start them with:

```bash
docker start stupefied_dewdney enterprise-rag-redis
```

The existing `enterprise_documents` collection and indexed sample corpus can be
used directly; re-ingestion is unnecessary for verification.

Tests:

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s evaluation/tests -p 'test_gener*.py' -v
python -m unittest discover -s evaluation/tests -p 'test_retrieval_metrics.py' -v
# Integration test: requires Qdrant, model weights, sample corpus, and OpenAI access
python -m unittest discover -s evaluation/tests -p 'test_tenant_isolation.py' -v
```

API tests use test JWT configuration and mocked external work; they do not need
services, paid generation calls, or model downloads. Pipeline integration tests
use the actual orchestration, with the Cross-Encoder constructor and retrieval /
generation/cache work mocked. The generation retry fixture now includes `usage=None`
to match the existing provider-response interface introduced by usage tracking.

## Swagger / Postman verification

1. Use the existing running services and indexed sample tenants, start the API on
   port 8001 if needed, then check `/health` and `/ready`. Dependency-failure 503
   behavior is covered by automated tests; no service shutdown is needed here.
2. Obtain a short-lived token from a trusted local administrator. For local
   development only, use the existing helper with a known user/tenant mapping:
   `python -c 'from src.auth.jwt_handler import create_access_token; print(create_access_token("dev-acme", "acme", "employee"))'`.
   Treat its output as a credential; do not paste tokens into logs or commits.
3. Open http://127.0.0.1:8001/docs; use Authorize to enter the token. In Postman,
   select Bearer Token authorization for the POST request.
4. Send `{"query":"What is the remote work allowance?","use_cache":false}` to
   `/api/v1/query`. Confirm answer and citation metadata match ACME evidence.
5. Issue a trusted GLOBEX token and repeat the same request; confirm GLOBEX
   evidence. Repeat with cache enabled for each tenant to verify isolation on HIT.
6. Remove/change/expire the token: expect 401. Add body `tenant_id`: expect 422.
   Send blank query, wrong types or `final_limit > retrieval_limit`: expect 422.
   An `X-Tenant-ID` header or URL tenant parameter must not change token identity.
7. With tracing enabled, inspect `rag.query` spans for tokens/costs and cache status.
   Compare MISS/BYPASSED versus HIT. Failure responses and diagnostic output must
   contain no raw questions, answers, credentials, or provider exception details.

## Remaining limitations

No live provider or retrieval success is implied by mocked API tests. Readiness is
a local dependency check, not an end-to-end generation probe. No rate limits,
body-byte limit, query cancellation policy, per-request deadline, issuer/audience
policy, token revocation/key rotation, production TLS/proxy configuration, HTTP
tracing propagation, or dependency lockfile is added in Day 26. JWTs retain the
existing single-secret HS256 trust model. Synchronous pipeline stages constrain
concurrency; they were not moved or rewritten. Child spans no longer contain raw
exception events; root failure status and usage metrics remain available. Existing
retrieval limits and empty-context behavior need later work. Production deployment
and Day 27–30 remain outside this change.

## Verification results (November 16, 2026)

- API and actual-orchestration HTTP tests: **9 passed**.
- Existing generation metrics, citation validation and retry tests: **22 passed**.
  The initial retry run had three fixture errors (`usage` absent); the corrected
  provider-response fixture resolves them without changing generator behavior.
- Existing retrieval metrics: **8 passed**. Run with `HF_HUB_OFFLINE=1
  TRANSFORMERS_OFFLINE=1` to use cached weights and avoid model metadata downloads.
- Initial Day 26 live tenant run: **4 errors** from Qdrant connection failures.
  This historical result is superseded by the TASK-026 verification below.
- Python compilation and `git diff --check`: passed.
- Tested environment: Python 3.13.3, FastAPI 0.163.1, Starlette 1.10.0,
  Uvicorn 0.54.0, Pydantic 2.13.5. New framework constraints permit compatible
  upgrades; the repository still does not have a complete dependency lockfile.

At the initial verification stage, no commits or pushes had been made. Day 27–30 were not implemented. Day 26 is now being finalized in a local commit at the user’s request; no push is authorized.


## TASK-026 environment verification (November 16, 2026)

Read-only checks confirmed the running container/host-port mappings above,
`enterprise_documents` collection existence through the application's Qdrant
client, Redis PING through the application's cache client, and HTTP 200 from
both `http://127.0.0.1:8001/health` and `/ready`.

The operator also verified an authenticated ACME query returning HTTP 200 with
a grounded answer and a citation from `acme-policy.pdf`. That manual result is
operator-reported; this follow-up does not generate, print, or record JWT tokens.

The original sandbox cannot access the Docker socket or local services. Service
checks and live tests therefore require execution with network/socket access;
sandbox connection failures do not establish that a service is down.

Review found **no environment-specific application code defect**. Retrieval and
readiness use the same existing Qdrant client and collection constant. Redis
cache, jobs, and broker target the intended host port and databases. The FastAPI
port is a Uvicorn option. Only this guide needed corrections for the existing
container name, port conflict, and outdated service-availability result. No
containers or collections were renamed, deleted, or recreated.

Final rerun results:

- Four live tenant-isolation tests: **4 passed**, 7.533 seconds. This exercises
  ACME/GLOBEX tenant filtering and excludes the other tenant's allowance text.
- API and actual-orchestration regression suite: **9 passed**, 10.179 seconds.
- `/health` and `/ready` on port 8001: **HTTP 200** each.
- Qdrant collection existence and Redis cache PING: **passed**.
- `git diff --check`: **passed**.

Live test command (run with local service/network access):

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 RAG_TRACING_ENABLED=false \
  .venv/bin/python -m unittest discover -s evaluation/tests -p 'test_tenant_isolation.py' -v
.venv/bin/python -m unittest discover -s tests -v
```

The live suite emitted non-failing unclosed socket/transport ResourceWarnings.
Its process-global async clients are not explicitly closed by these existing
isolated-event-loop tests; client lifecycle cleanup remains a follow-up concern,
not evidence of an endpoint/container configuration failure. No code change was
made for this warning in TASK-026. No JWT tokens or secrets were recorded.


## Final pre-commit regression verification (November 16, 2026)

After the README startup/authentication/port update, all **43 tests passed**:
9 API/orchestration, 22 generation, 8 retrieval metrics, and 4 live tenant-isolation
tests. The live suite retained the non-failing socket/transport cleanup warnings
noted above. The sandboxed retrieval-metric run also emitted a Qdrant version-check
warning; its metric tests passed, and the live suite passed with service access.
`git diff --check` passed. Pending files were reviewed for credentials and generated
artifacts; only source, tests, requirements, README, and this guide are included
in the Day 26 commit. Test signing keys are synthetic fixtures, not operational
credentials. Environment findings above remain unchanged. No push or Day 27 work
is included.
