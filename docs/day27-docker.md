# Day 27 — Docker Compose deployment

## Audit and scope

The Day 26 baseline had no Dockerfile, Compose file, or Docker build exclusions.
Qdrant used `localhost:6333` / `enterprise_documents`; Redis used `localhost:6381`
with database 0 for the Celery broker, 1 for job states, and 2 for caches. The
worker entry point is `src.workers.celery_app:celery_app`. Ingestion accepts a PDF
filesystem path and performs paid OpenAI embedding requests. Existing ingestion
experiments are explicit development scripts, not safe startup hooks.

The API loads OpenAI clients and a Sentence Transformers Cross-Encoder at startup.
Previously the reranker name was fixed and Hugging Face used its default user
cache; weights were not part of the repository. The existing async RAG pipeline,
JWT validation, tenant filters, cache identities, retries, and usage tracking are
unchanged. Configuration now comes from `src/config.py`, preserving all original
host-based defaults when environment variables are absent.

Only TASK-027 is implemented. No deployment command migrates, resets, or re-ingests
the existing local corpus.

## Architecture

| Service | Internal address | Storage / purpose |
| --- | --- | --- |
| `api` | `api:8000` | Existing FastAPI app; host `127.0.0.1:${API_PORT:-8001}` |
| `worker` | Redis broker | Existing Celery ingestion task, one worker process |
| `qdrant` | `qdrant:6333` | Project-scoped `qdrant_data` named volume |
| `redis` | `redis:6379` | Project-scoped `redis_data` named volume; AOF enabled |

Redis and Qdrant have **no published host ports**. Compose service DNS routes the
application to its own services, not to host localhost or pre-existing containers.
There are no fixed container names or external volumes. Project names scope
containers, network, and volumes. The API host binding is loopback-only.

API and worker share the same non-root CPU image, read-only document bind mount,
and `model_cache` volume. Application containers drop capabilities and disallow
privilege escalation. `init: true` forwards signals/reaps children. Dependencies
wait for Qdrant's `/readyz` and Redis PING. API health uses `/ready`; worker health
uses a targeted Celery control ping to its hostname via the broker. The worker
has a 60-second shutdown grace period. Restart policy
is `unless-stopped`; health failure itself does not automatically restart a
running container. No automatic collection initialization or ingestion occurs.

Python, Qdrant, and Redis image digests are pinned. `requirements-docker.txt` pins
the resolved Linux CPU dependency versions while `requirements.txt` remains the
local development requirements. CPU PyTorch is installed from its official CPU
wheel index, avoiding CUDA dependencies. Model revision is pinned in Compose.
Builds require internet access to registries/package indexes. The first API start
downloads public Hugging Face weights into `model_cache`; subsequent starts reuse
them. No OpenAI calls are made merely by starting services.

## Environment variables

Copy the template to a **separate ignored file**; do not overwrite the existing
local `.env`:

```bash
cp .env.example .env.compose
```

Edit `.env.compose` locally. Supply `OPENAI_API_KEY` and a strong independently
generated `JWT_SECRET_KEY`; Compose refuses missing/blank values. Secrets are
runtime environment variables, never build arguments, image layers, or template
values. Avoid printing resolved Compose configuration or container environments.
`docker compose config --quiet` validates without displaying secrets.

| Variable | Host Python default | Compose value / behavior |
| --- | --- | --- |
| `OPENAI_API_KEY` | Required for OpenAI clients | Required runtime value; not checked for provider validity at startup |
| `JWT_SECRET_KEY` | Required for JWT verifier | Required runtime value; existing HS256 verification unchanged |
| `API_PORT` | Uvicorn CLI selects port | Published API port, default `8001`; use `18001` alongside the local API |
| `APP_IMAGE` | N/A | Image/tag, default `enterprise-rag-app:day27` |
| `QDRANT_URL` | `http://localhost:6333` | Compose explicitly sets `http://qdrant:6333` |
| `QDRANT_COLLECTION` | `enterprise_documents` | Same default; a new project's collection must be initialized explicitly |
| `REDIS_CACHE_URL` | `redis://localhost:6381/2` | Compose explicitly sets `redis://redis:6379/2` |
| `REDIS_JOBS_URL` | `redis://localhost:6381/1` | Compose explicitly sets `redis://redis:6379/1` |
| `CELERY_BROKER_URL` | `redis://localhost:6381/0` | Compose explicitly sets `redis://redis:6379/0` |
| `GENERATION_MODEL` | API default `gpt-4.1-mini` | Same default |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Same default; vector dimension remains 1536 |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L6-v2` | Same default |
| `RERANKER_REVISION` | Unspecified / upstream default | `233902d25c440f23af6f7d6e94d2946bac0bee0a` |
| `HF_HOME` | Hugging Face library default for host runs | `/home/app/.cache/huggingface`, a shared named volume |
| `DOCUMENTS_DIR` | Host script's input path | Read-only mount source, default `./data`, visible at `/app/data` |
| `RAG_TRACING_ENABLED` | `false` | `false`; existing console exporter can be enabled |

Compose deliberately overrides host service URLs with its internal DNS addresses;
the host defaults in `.env.example` are for direct Python execution. Do not point
the verification stack at existing services. Environment settings are read at
import/startup; restart/recreate only the new application's services after edits.

Changing model names does not add model support: existing pricing tables support
`gpt-4.1-mini` and `text-embedding-3-small`, and the Qdrant vector size is 1536.
Other embeddings may require separately designed collections and re-indexing.
Do not change model/dimension settings for an existing corpus casually.

## Safe first-time startup alongside the existing environment

Existing local containers remain `stupefied_dewdney` (6333) and
`enterprise-rag-redis` (6381). The existing API uses 8001; Portainer uses 8000.
The following project uses **18001**, with separate empty volumes. It does not
share existing storage. Run from the repository root:

```bash
# Uses .env.compose; does not read the local .env for Compose interpolation.
# Shell variables override file values, so 18001 is guaranteed for these commands.
dc() { API_PORT=18001 docker compose --env-file .env.compose -p enterprise-rag-docker "$@"; }
dc config --quiet
dc build api
dc up -d --wait qdrant redis
# Explicitly creates the configured collection only if absent; no embeddings,
# documents, migration, deletion, or provider requests.
dc run --rm --no-deps api python -m src.vector_store.init_collection
dc up -d --no-build --wait --wait-timeout 600 api worker
```

For a separate machine where 8001 is available, set `API_PORT=8001` in the env file
and omit the inline `API_PORT=18001` override. Every command must use the same
project name and env file. Do not run a bare `docker compose up` alongside the
existing API without choosing a free API port.

Swagger is at http://127.0.0.1:18001/docs. `/health` and `/ready` should return 200.
Without the explicit initialization step, Qdrant can be healthy while the API's
`/ready` returns 503 because the collection is absent. An empty initialized
collection passes readiness but cannot answer document questions.

Paid sample ingestion is **manual and optional**, after setting a real API key:

```bash
# Writes only to this Compose project's configured collection; makes paid calls.
dc run --rm --no-deps api python -m experiments.tenant_ingestion
```

For background ingestion, submit jobs from inside this project, using paths like
`/app/data/employee-handbook.pdf` that the worker can read. The included
`experiments.test_background_ingestion` resolves to that container path when run
inside the image. Never submit a host-only absolute path to the container worker.
There is no new upload or task-submission HTTP endpoint in TASK-027.

## Stop, restart, and persistence

```bash
dc stop
# Resume the same project/volumes:
dc start
# Or remove only this project's containers/network; all named volumes remain:
dc down
```

Do **not** add a volume-deletion flag. Never use global Docker prune commands for
this task. `down` without volume removal, `stop/start`, and normal restarts retain
Qdrant data, Redis AOF, and downloaded model weights. Changing the project name
selects different volumes and can make data appear absent. Named volumes are not
backups; provide external backups before any real production deployment.

The TASK-027 verification project is named `rag-day27-verify` and uses image
`enterprise-rag-day27-verify:local`. Its temporary containers/network can be removed
with that exact project's `down`; its named volumes are intentionally retained.
No cleanup command should mention the pre-existing local container names.

## Tests and manual infrastructure verification

Host tests (no paid provider calls):

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s evaluation/tests -p 'test_gener*.py' -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest discover -s evaluation/tests -p 'test_retrieval_metrics.py' -v
```

The configuration tests verify unchanged host defaults and environment routing
into Qdrant, both cache clients, job storage, Celery broker, and embedding defaults.
Do not run the existing live retrieval/ingestion suites for infrastructure checks:
they can call OpenAI. Use the mocked suites in the image instead:

```bash
dc run --rm --no-deps api python -m unittest discover -s tests -v
dc run --rm --no-deps api python -m unittest discover -s evaluation/tests -p 'test_gener*.py' -v
```

Manual checks after startup:

```bash
curl --fail http://127.0.0.1:18001/health
curl --fail http://127.0.0.1:18001/ready
dc ps
dc exec redis redis-cli ping
dc exec worker sh -c 'celery -A src.workers.celery_app:celery_app inspect ping --destination "worker@$HOSTNAME" --timeout 5'
dc exec api python -c 'import asyncio; from src.vector_store.qdrant_store import client, COLLECTION_NAME; print(asyncio.run(client.collection_exists(COLLECTION_NAME)))'
```

No authenticated query is needed to verify infrastructure. For a no-spend smoke
stack, use a dummy nonempty OpenAI key and a verification-only JWT secret in a
private env file; do not submit generation/ingestion tasks. Public model downloads
are separate from OpenAI calls. Never reuse verification credentials operationally.

To verify persistence manually, create a uniquely named probe collection/point
and Redis key **only in the new project**, restart its Qdrant/Redis services,
then read the same point/key. Remove only your probe records after the check;
retain volumes and real collection contents. TASK-027 performs this check without
copying or modifying the existing corpus.

## Troubleshooting and limits

- Port conflict: choose a free `API_PORT`; changing a Compose project name alone
  does not isolate a published host port. Redis/Qdrant ports are internal only.
- Unhealthy API: check `dc logs api` for startup/model errors, confirm public model
  download access, named-cache permissions, and collection initialization. A slow
  first model download may exceed the wait timeout; extend it and retry startup.
- Unhealthy worker: check `dc logs worker`, Redis health, and destination hostname.
  It uses one prefork process to limit memory use; this is not a throughput tuning
  exercise. Existing ingestion clients are process-global and task loop cleanup
  remains a known limitation from the host implementation.
- Host sandbox socket/network denial does not mean Docker/services are down.
- Secrets are visible to users with Docker daemon access through container
  inspection. Use an appropriate secret manager/orchestrator for shared production
  hosts. JWT behavior is unchanged; TLS, revocation, and tenant provisioning remain
  outside this task. Database ports are not published and the API is loopback-only.
- Dependencies are pinned by version, not wheel hashes; Python and service image
  digests are immutable. Refresh pins/model revisions deliberately and rerun tests.
  Runtime verification currently targets Linux CPU on the available architecture;
  other architectures and GPU deployment require independent validation.
- The image excludes `.env*`, Git history, local storage, caches, generated reports,
  and PDF contents. PDFs are supplied by a read-only bind mount. It includes tests
  and existing experiment scripts for developer workflows, not auto-execution.
- No paid end-to-end answer quality or live tenant-corpus regression is implied
  by infrastructure checks. Existing RAG limits (BM25 cap, empty corpus behavior,
  synchronous stages, cache invalidation) are unchanged. TASK-028 is not started.

## Verification results

Verified on November 16, 2026, using Docker 27.5.1 / Compose v2.32.4 on Linux
ARM64 (Docker Desktop). Results:

- Compose `config --quiet`: passed; missing/blank secret values fail validation.
- Image build with pinned dependencies and `pip check`: passed.
- Host regression suites: **42 passed** (12 API/configuration, 22 generation,
  8 retrieval metrics). Host retrieval metrics emitted the existing non-failing
  sandbox Qdrant version-check warning.
- Built Linux image: the same **42 tests passed**. API/configuration and generation
  suites ran with container networking disabled; retrieval metrics used the
  downloaded model cache with Hugging Face offline mode.
- All four Compose services became healthy; host port 18001 health/readiness:
  **200/200**. An unauthenticated query returned **401**, before any pipeline call.
- Internal Qdrant collection check, Redis cache/job PING, and Celery broker control
  ping: passed. Worker reported one online node.
- A synthetic Qdrant point and Redis marker survived normal Qdrant/Redis restarts.
  Only the task-owned probe collection/key were subsequently removed.
- The image runs non-root and contains no `.env` files, baked API/JWT credentials,
  Git metadata, local Qdrant storage, or generated evaluation reports.
- Existing container IDs remained `caead2ed8008` (`stupefied_dewdney`) and
  `3a2cbf4bb426` (`enterprise-rag-redis`), with their original mounts/ports. The
  original collection remained available and the existing API on 8001 still
  returned **200/200** for health/readiness.
- No paid OpenAI requests, token creation, ingestion, corpus migration, container
  replacement, or volume deletion was performed. Live corpus retrieval tests were
  intentionally not rerun because they can make paid embedding requests.
- `git diff --check` and Python compilation passed. No commit or push was made
  during the initial implementation/verification stage.

Manual infrastructure checks after startup (no provider requests):

```bash
dc ps
curl --fail http://127.0.0.1:18001/health
curl --fail http://127.0.0.1:18001/ready
dc exec redis redis-cli ping
dc exec worker celery -A src.workers.celery_app:celery_app inspect ping --timeout 5
dc exec -T api python -c 'import os,httpx; r=httpx.get("http://qdrant:6333/collections/"+os.environ["QDRANT_COLLECTION"]); r.raise_for_status(); print("points:",r.json()["result"]["points_count"])'
# Normal restart, then repeat the checks/count; volumes and data are retained.
dc restart qdrant redis
dc up -d --no-build --wait --wait-timeout 120
```

In the verification project, a separate task-owned probe collection with a single
synthetic 1D vector and a Redis marker were used to test persistence. Only that
probe collection/key were removed after the restart check; the application
collection and all named volumes were retained. No PDFs were indexed and no
OpenAI embedding/generation requests were made.


Final runtime outcome: all four services became healthy; host port 18001 returned
HTTP 200 for both probes, and unauthenticated query requests returned 401 before
pipeline execution. Redis databases 1/2 were reachable, the worker returned `pong`
through database 0, and the configured Qdrant collection was available. A synthetic
Qdrant point and Redis value survived a normal database restart and were then
removed from only the task-owned probe collection/key. Offline retrieval metrics
also verified reuse of the downloaded model cache.

The verification containers and project network were removed using normal
`down`, with no volume deletion. The three `rag-day27-verify_*` named volumes
remain, and the tested application image is retained as
`enterprise-rag-app:day27` (the temporary verification tag was removed). Original
container IDs, published ports, and collection availability were checked again
and remained intact. No secrets were baked into the image or found in pending
changes. No commits, pushes, or TASK-028 work were performed during that
implementation stage.

Live corpus isolation tests were intentionally not rerun in TASK-027 because they
can make paid embedding requests; their prior Day 26 results remain in
`docs/day26-api.md`. Infrastructure tests used a non-operational OpenAI placeholder
and never submitted an authenticated RAG query or ingestion job.

## File manifest

Created: `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`,
`requirements-docker.txt`, `src/config.py`,
`src/vector_store/init_collection.py`, `tests/test_config.py`, and this guide.

Modified: `.gitignore`, `README.md`, `src/cache/embedding_cache.py`,
`src/cache/result_cache.py`, `src/ingestion/embedder.py`, `src/jobs/job_store.py`,
`src/retrieval/reranker.py`, `src/vector_store/qdrant_store.py`, and
`src/workers/celery_app.py`. The Day 26 environment guide is unchanged.


## Finalization verification

The operator manually confirmed all four Compose services healthy, Swagger UI
available on port 18001, and HTTP 401 for an unauthenticated query. The final
regression rerun passed all **42 tests** (12 API/configuration, 22 generation,
8 retrieval metrics), along with quiet Compose validation, Python compilation,
and diff checks. The retrieval-metric run retained the documented non-failing
sandbox Qdrant version-check warning.

Only the TASK-027 source, tests, deployment configuration, dependency pins, and
documentation are included in the authorized local commit. Private `.env` /
`.env.compose` files, credentials, Docker volumes, caches, and generated artifacts
are excluded. No paid provider requests, push, or TASK-028 work is included.
