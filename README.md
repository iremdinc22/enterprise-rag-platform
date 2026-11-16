# Enterprise RAG Platform

A multi-tenant document question-answering platform with authenticated FastAPI
access, hybrid retrieval, validated citations and background PDF ingestion.

The retrieval pipeline combines BM25 and Qdrant vector search with reciprocal
rank fusion, Cross-Encoder reranking and MMR. Answers retain source metadata;
JWT claims determine tenant scope throughout retrieval and answer caching.
Redis supports caches and job state, Celery handles asynchronous ingestion, and
OpenTelemetry spans plus request-local usage tracking support evaluation.

## Documentation

- [Architecture](docs/architecture.md): components, data flow, tenant isolation and technical limits.
- [API](docs/api.md): endpoints, JWT requirements, schemas, errors and Swagger/Postman checks.
- [Deployment](docs/deployment.md): local setup, Docker Compose, configuration and persistent storage.
- [Local demo](docs/demo.md): isolated configuration, authentication and placeholder; startup requires approval.
- [Demo uploads](docs/demo-uploads.md): PDF admission/job tracking contracts; provider remains disabled.
- [Testing](docs/testing.md): deterministic E2E coverage, controlled live testing, results and limitations.

## Run locally

Use existing configured services and a private `.env` with provider credentials
and `JWT_SECRET_KEY`. Do not overwrite environment files or initialize/re-ingest
an existing corpus.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001 --no-access-log
```

The host defaults target Qdrant `localhost:6333`, collection
`enterprise_documents`, and Redis `localhost:6381` (broker DB 0, job DB 1,
cache DB 2). Port 8000 is occupied by Portainer in the verified environment;
the local API uses 8001. See [configuration](docs/deployment.md#configuration)
for model names, service URLs and environment requirements.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/query` | JWT-authenticated tenant-scoped query |
| `GET /health` | Process liveness |
| `GET /ready` | Configured Qdrant collection availability |
| `GET /docs` | Swagger UI |

Query tokens require verified HS256 claims including tenant, subject, role and
expiry. Client-supplied tenant IDs are rejected; there is no public token-issuance
or ingestion endpoint. [API examples and authentication details](docs/api.md)
explain the exact contract. Readiness does not check OpenAI billing or answer quality.

## Docker Compose

The deployment includes API, Celery worker, Qdrant and Redis with project-scoped
persistent volumes and internal service DNS. Only the loopback API port is
published; it defaults to 8001 and is configurable with `API_PORT`. The verified
existing Compose deployment uses 18001, separate from local services.

For a new deployment, follow the [safe first-time instructions](docs/deployment.md#safe-first-time-startup)
to choose a free port/project, prepare a private `.env.compose`, build and explicitly
initialize an empty collection. Startup does not migrate or ingest the local
corpus. Normal shutdown retains volumes; never add a volume-deletion flag.

## Tests and verified behavior

Install test-only dependencies with `python -m pip install -r requirements-test.txt`
before running the host regression suite. They are not application dependencies.

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s evaluation/tests -p 'test_gener*.py' -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest discover -s evaluation/tests -p 'test_retrieval_metrics.py' -v
./scripts/test-e2e.sh
```

The deterministic Docker suite covers 18 scenarios using real HTTP/database/job
components and fake providers, without paid calls or access to the local corpus.
Offline retrieval tests require previously cached reranker weights.

The final separately approved live smoke test returned HTTP 200 for ACME
(**500 USD**, `acme-policy.pdf`), GLOBEX (**2000 USD**, `globex-policy.pdf`) and
a pinned ACME cache repeat, with no cross-tenant citations. Estimated provider
cost was **USD 0.00124356**, with **USD 0.00576920** retained reservation.
[Testing](docs/testing.md) explains the guard, call counts, cache semantics and
limitations. Further live runs require explicit approval and may incur charges.

This repository provides a developer deployment and measured functional coverage.
Operational controls such as TLS, backups, token revocation and tenant provisioning
require separate deployment design; current pipeline limitations are documented.
