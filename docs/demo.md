# Isolated local demo infrastructure

[Overview](../README.md) · [Deployment](deployment.md)

TASK-030.3 supplies infrastructure only. There is no React dashboard, demo login,
upload/document API, or simulated provider yet. The frontend is a static Python
placeholder, to be replaced by React/TypeScript/Vite and its same-origin API proxy.
**Do not start this stack until explicitly approved.** Commands below are for that
later approved operation; configuration validation/tests do not start services.

## Isolation and configuration

Use [compose.demo.yaml](../compose.demo.yaml) alone, with project name
`enterprise-rag-demo`. Do not merge it with another Compose file or attach existing
networks/volumes. It publishes only `127.0.0.1:${DEMO_PORT:-18030}:5173`; API (8000),
Qdrant (6333) and Redis (6379) remain private. All five services use a project-scoped
internal backend network, without Internet routing. Only the secret-free frontend
also joins a separate project-scoped edge network, required for effective loopback
port publishing in the verified Docker Desktop runtime. No backend joins edge.
Check the selected host port before
startup; 18030 is proposed, not verified free.

Project-scoped named volumes are `uploads`, `qdrant_data`, `redis_data`, and
`model_cache`. There are no host-data bind mounts or external volumes. API mounts
`/demo/uploads` read/write; worker mounts it read-only. The image provides an
app-owned mount target (UID/GID 10001); permission behavior still needs runtime
verification on first approved startup. Model cache is separate from existing
model volumes. Normal shutdown preserves all volumes.

Backend/worker entrypoints validate settings **before importing existing clients**:

| Setting | Required demo value |
| --- | --- |
| `DEMO_ENABLED` | `1` |
| `DEMO_PROVIDER_MODE` | `disabled` (only supported mode in this task) |
| `DEMO_JWT_SECRET` | New demo-only secret, 32–256 printable non-space ASCII characters |
| `PYTHON_DOTENV_DISABLED` | `1` |
| `QDRANT_URL` | `http://qdrant:6333` |
| `QDRANT_COLLECTION` | `enterprise_demo_documents` |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` |
| `REDIS_JOBS_URL` | `redis://redis:6379/1` |
| `REDIS_CACHE_URL` | `redis://redis:6379/2` |
| `DEMO_UPLOAD_ROOT` | `/demo/uploads` |
| `HF_HOME` | `/home/app/.cache/huggingface` |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | `1` |
| `RAG_TRACING_ENABLED` | `false` |
| `DEMO_MAX_UPLOAD_BYTES` | 1–5242880; Compose default 5242880 |
| `DEMO_MAX_PDF_PAGES` | 1–10; Compose default 10 |
| `DEMO_MAX_CHUNKS` | 1–200; Compose default 200 |

Upload limits are validated configuration for future upload work, **not an
implemented upload policy or API**. ACME/GLOBEX session mapping is also deferred.
Redis DB separation and existing cache/job key formats remain unchanged; isolation
comes from the separate Redis instance. Future demo document metadata will have
its own namespace. There is no metadata store implementation in this task.

No `OPENAI_API_KEY` or existing `JWT_SECRET_KEY` is forwarded. The entrypoint maps
`DEMO_JWT_SECRET` to the existing JWT utility only inside its process. Inherited
provider/JWT credentials and already imported backend clients/config are rejected.
Freshness of a user-supplied secret cannot be proven; never reuse existing secrets.
The frontend receives no secret or upload/model volume.

Authenticated queries return sanitized 503 before query admission/retrieval/cache;
invalid tokens still return 401. Demo startup skips eager RAG/reranker loading.
Process-local disabled SDK clients reject embedding/generation access before any
provider transport, including worker batch embeddings. `deterministic` and `openai`
are rejected until separately implemented and approved. Production entrypoints,
provider clients, and existing ingestion behavior are unchanged.

## Static validation (safe without startup)

Use a clearly fake validation value, not an existing secret:

```bash
DEMO_JWT_SECRET=validation-only-not-a-real-secret-000000000000 \
  docker compose --env-file /dev/null -f compose.demo.yaml config --quiet
python -m unittest tests.test_demo_infrastructure -v
python -m compileall -q src tests
git diff --check
```

Tests parse Compose locally (no daemon/startup), check port/network/volume rules,
reject unsafe configuration, and exercise API/worker initialization with fake
clients or blocked sockets. If Docker CLI is absent, Compose-specific tests skip;
configuration parsing is not proof that containers will start successfully.
Never print resolved Compose configuration with real credentials.

## Later approved first startup

First create a **new** private `.env.demo`; do not overwrite `.env`, `.env.compose`
or a previous `.env.demo`. [.env.demo.example](../.env.demo.example) describes the
fields. Generate the secret without printing it, with exclusive creation:

```bash
python - <<'PY'
import os, secrets
fd = os.open('.env.demo', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('DEMO_JWT_SECRET=' + secrets.token_urlsafe(48) + '\n')
    stream.write('DEMO_PORT=18030\nDEMO_PROVIDER_MODE=disabled\n')
PY
```

`.env.demo` is ignored; the tracked example contains no secret. Choose another
loopback port if 18030 is occupied. Do not add host service URLs, provider keys,
existing secrets, or volume names to this file.

After explicit startup approval:

```bash
docker compose --env-file .env.demo -f compose.demo.yaml config --quiet
docker compose --env-file .env.demo -f compose.demo.yaml build
docker compose --env-file .env.demo -f compose.demo.yaml up -d qdrant redis
# Explicit creation of ONLY the demo collection; no documents or embeddings.
docker compose --env-file .env.demo -f compose.demo.yaml run --rm --no-deps api \
  python -m src.demo.run init-collection
docker compose --env-file .env.demo -f compose.demo.yaml up -d
docker compose --env-file .env.demo -f compose.demo.yaml ps
```

Browse `http://127.0.0.1:18030` for the placeholder only. It has no API proxy yet.
API health uses `/health`; `/ready` remains a separate collection-existence check
and returns 503 until explicit initialization. Neither proves provider availability
or end-to-end ingestion readiness. Worker health uses targeted Celery ping.
There is no automatic collection creation, seeding, corpus copy or ingestion.

Normal shutdown (retains data):

```bash
docker compose --env-file .env.demo -f compose.demo.yaml down
```

Do not append volume-deletion flags or run global cleanup/prune commands. Reset
and persistent-data deletion require separate approval. Nothing here targets
`stupefied_dewdney`, `enterprise-rag-redis`, `enterprise-rag-docker`, their volumes,
or collection `enterprise_documents`.

## Remaining verification

This task does not start/build the stack. On approved startup, verify non-root
volume access, internal networking plus loopback browser access, each healthcheck,
collection initialization, and existing-environment preservation. The frontend is
not Vite yet. No login, uploads, jobs API, document list or PDF viewer is supplied.
The demo is localhost-first infrastructure, not a public deployment or a hard
billing-cap mechanism.
