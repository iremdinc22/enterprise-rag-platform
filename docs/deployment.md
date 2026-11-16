# Deployment

[Overview](../README.md) · [Architecture](architecture.md) · [API](api.md) · [Testing](testing.md)

## Local development and existing services

Install the host dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001 --no-access-log
```

Supply private environment values before starting. Configuration is loaded at
import/startup; do not overwrite an existing `.env`. The API entry point loads
provider clients and the Cross-Encoder, whose weights must be downloaded or cached.
Starting the application does not itself submit OpenAI requests.

The verified development environment is distinct from Compose-managed storage:

| Existing resource | Address / purpose |
| --- | --- |
| `stupefied_dewdney` | Qdrant at `localhost:6333`; collection `enterprise_documents` |
| `enterprise-rag-redis` | Redis at `localhost:6381` |
| Local FastAPI | `127.0.0.1:8001` |
| Portainer | Occupies port `8000` |
| Existing Compose project `enterprise-rag-docker` | API at `127.0.0.1:18001`, separate databases |

Do not recreate these containers, initialize another corpus into them, or reuse
occupied ports for a new stack. An unrelated Redis also occupies host port 6379.
If the existing local containers are stopped, start them without recreating them:

```bash
docker start stupefied_dewdney enterprise-rag-redis
```

If these existing local containers are stopped, resume them with
`docker start stupefied_dewdney enterprise-rag-redis`; do not replace them.
The existing local collection contains ACME and GLOBEX documents; it needs no
initialization or re-ingestion for the approved corpus tests.

## Compose architecture

[compose.yaml](../compose.yaml) runs four services:

| Service | Internal address | Role |
| --- | --- | --- |
| `api` | `api:8000` | FastAPI; publishes `127.0.0.1:${API_PORT:-8001}` |
| `worker` | Celery broker | PDF ingestion worker, one prefork process |
| `qdrant` | `qdrant:6333` | Vector storage; project-scoped `qdrant_data` volume |
| `redis` | `redis:6379` | Broker, job states, caches; `redis_data` volume with AOF |

Only the API publishes a host port. Service DNS, project-scoped networks/volumes,
and the absence of fixed container names keep this deployment separate from the
host databases. API and worker share `model_cache` and a read-only document mount
at `/app/data`. No startup hook initializes collections or ingests documents.

The [Dockerfile](../Dockerfile) uses digest-pinned Python 3.13.3 slim Bookworm,
CPU PyTorch, and [Linux dependency pins](../requirements-docker.txt). Qdrant and
Redis images are digest-pinned. The application runs as UID/GID 10001, drops Linux
capabilities, disallows privilege escalation, and uses an init process. Builds
need registry/package access; first startup may download public Hugging Face
weights. Secrets, local PDFs, Git metadata, caches, and generated reports are
excluded from image build input by [.dockerignore](../.dockerignore).

## Configuration

For a new deployment only, create a separate ignored env file without replacing
an existing one:

```bash
test -e .env.compose || cp .env.example .env.compose
```

Edit it privately using [.env.example](../.env.example) as the reference. API keys
and JWT signing secrets are required nonblank runtime variables, never build
arguments. Do not print resolved Compose configuration or container environments.
Use `config --quiet` for validation.

| Variable | Direct Python default | Compose setting |
| --- | --- | --- |
| `OPENAI_API_KEY` | Required for provider clients | Required; validity/quota not checked by readiness |
| `JWT_SECRET_KEY` | Required for token verification | Required; HS256 verification |
| `API_MAX_BODY_BYTES` | `32768` | API request body byte limit |
| `API_BODY_TIMEOUT_SECONDS` | `5` | Total query body receive deadline |
| `API_QUERY_DEADLINE_SECONDS` | `60` | API-only absolute query deadline; blocking cleanup can delay 504 |
| `API_MAX_ACTIVE_QUERIES` | `2` | Local API admission slots; no waiting queue |
| `API_TENANT_RATE_LIMIT` | `6` | Accepted queries per verified tenant/window |
| `API_GLOBAL_RATE_LIMIT` | `12` | Accepted queries globally/window |
| `API_RATE_WINDOW_SECONDS` | `60` | Rolling admission window; monotonic time |
| `API_DEMO_MAX_QUERIES` | `50` | Accepted queries per app process lifetime; no refunds |
| `API_PORT` | Set by Uvicorn CLI | Host port, default `8001` |
| `APP_IMAGE` | Not applicable | `enterprise-rag-app:day27` |
| `QDRANT_URL` | `http://localhost:6333` | Explicitly `http://qdrant:6333` |
| `QDRANT_COLLECTION` | `enterprise_documents` | Same default, separate project storage |
| `CELERY_BROKER_URL` | `redis://localhost:6381/0` | `redis://redis:6379/0` |
| `REDIS_JOBS_URL` | `redis://localhost:6381/1` | `redis://redis:6379/1` |
| `REDIS_CACHE_URL` | `redis://localhost:6381/2` | `redis://redis:6379/2` |
| `GENERATION_MODEL` | `gpt-4.1-mini` | Same default |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Same default; collection vectors remain 1536-dimensional |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L6-v2` | Same default |
| `RERANKER_REVISION` | Upstream default | `233902d25c440f23af6f7d6e94d2946bac0bee0a` |
| `HF_HOME` | Library default | `/home/app/.cache/huggingface` on `model_cache` |
| `DOCUMENTS_DIR` | Input script path | `./data`, mounted read-only at `/app/data` |
| `RAG_TRACING_ENABLED` | `false` | `false`; optional console spans |

Compose overrides host database URLs deliberately. Shell variables override env
file values. Keep Redis DBs 0/1/2 separate. A different embedding model is not a
configuration-only migration: dimensions, indexed vectors, and pricing support
must be considered separately. Do not change the existing corpus for deployment.

Admission counters are in memory and assume one application instance per process,
as in the provided deployment. Restart/recreating the application resets them;
workers/replicas multiply the allowances. They do not coordinate with Celery,
other provider clients, or provider billing. Existing env files are not changed.

## Safe first-time startup

The following is for a **new** project on a free port, not the already running
`enterprise-rag-docker` project. Choose another project/port if these are occupied.
Run from the repository root:

```bash
dc() { API_PORT=18002 docker compose --env-file .env.compose -p enterprise-rag-new "$@"; }
dc config --quiet
dc build api
dc up -d --wait qdrant redis
# Explicit creation if absent, only in this new project's empty storage.
# No ingestion, embeddings, migration, or deletion.
dc run --rm --no-deps api python -m src.vector_store.init_collection
dc up -d --no-build --wait --wait-timeout 600 api worker
```

Use the same project, port and env file for subsequent commands. On a clean
machine, port 8001 is the default; choose it only when available. Swagger is at
`http://127.0.0.1:18002/docs` for the example above. An initialized empty collection
passes readiness but has no document evidence. Qdrant can be healthy while API
readiness is 503 if its configured collection is missing.

Ingestion is a separate, paid, mutating operation and is **not an infrastructure
verification step**. For a deliberately new corpus, the existing explicit sample
entry point is `python -m experiments.tenant_ingestion`, run inside the selected
application environment. Do not run it against the existing local corpus.
Background jobs must use worker-readable paths such as
`/app/data/employee-handbook.pdf`, not host-only absolute paths. The worker entry
point is `src.workers.celery_app:celery_app`; there is no upload HTTP endpoint.

## Health and manual verification

```bash
curl --fail http://127.0.0.1:18002/health
curl --fail http://127.0.0.1:18002/ready
dc ps
dc exec redis redis-cli ping
dc exec worker sh -c 'celery -A src.workers.celery_app:celery_app inspect ping --destination "worker@$HOSTNAME" --timeout 5'
dc exec api python -c 'import asyncio; from src.vector_store.qdrant_store import client, COLLECTION_NAME; print(asyncio.run(client.collection_exists(COLLECTION_NAME)))'
```

No authenticated query or provider call is needed. `/ready` checks the Qdrant
collection; Redis is optional for API readiness because caches fail open. Compose
also waits for Redis PING and Qdrant `/readyz` before starting API/worker. API health
has a 180-second start period; worker health targets its Celery hostname, with a
60-second start period and shutdown grace period. `unless-stopped` restarts exited
containers; an unhealthy status alone does not restart a running process.

For no-spend infrastructure testing use non-operational, nonempty credentials in
a separate private env file and never submit generation/ingestion jobs. For fully
isolated integration verification use [the E2E stack](testing.md#deterministic-integration-tests).

## Persistence and shutdown

```bash
dc stop
dc start
# Remove this project's containers/network, retaining all named volumes:
dc down
```

Never add a volume-deletion flag or use global prune commands. Normal restarts
and these commands retain Qdrant data, Redis AOF, and model weights. A different
project name selects different volumes; named volumes are not backups.

Earlier infrastructure verification confirmed persistence using a uniquely named
synthetic Qdrant probe and Redis marker across a normal restart, then removed only
those probe records. It did not change the existing corpus. The earlier
`rag-day27-verify_*` volumes were intentionally retained; they are separate from
both the local environment and the current Compose project.

## Verified results and limitations

The operator confirmed all four existing Compose services healthy, Swagger on
18001, health/readiness HTTP 200, and unauthenticated query HTTP 401. Earlier build
verification passed dependency checks and 42 host/container regressions, confirmed
non-root execution and secret exclusions, and preserved the original Qdrant/Redis
container IDs and data. Current regression results are in [Testing](testing.md).

Runtime verification covered Docker 27.5.1 / Compose 2.32.4, Linux ARM64 CPU,
Python 3.13.3. Other architectures/GPU deployments need separate validation.
Dependencies are version-pinned rather than wheel-hash locked. TLS, secret
management, backups, tenant provisioning, token revocation and scaling are not
provided by this developer deployment. Docker-daemon users can inspect runtime
secrets. Protect that access.

For startup failures check the selected project's logs, model-download access,
cache permissions, collection existence and port availability. Extend the startup
wait for slow model downloads. Check broker health and worker hostname for Celery
health failures. Existing process-global async ingestion clients have event-loop
lifecycle limitations; production worker behavior is unchanged. Sandbox network
restrictions can deny host access without implying that Docker services are down.
