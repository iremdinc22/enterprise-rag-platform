# Isolated local demo infrastructure

[Documentation index](README.md) · [Overview](../README.md) · [Deployment](deployment.md)

The isolated demo provides server-side sessions and a complete document workflow.
Demo upload and job tracking contracts are in [the upload guide](demo-uploads.md).
The React + TypeScript dashboard provides login, tenant-owned document listing,
authenticated PDF downloads, upload/job tracking and cited queries. See
[dashboard development](demo-dashboard.md). The opt-in
[deterministic provider](demo-deterministic.md) runs locally; disabled remains the default.
Start only the isolated project after checking its configuration and host port.
Configuration validation does not start services.

## Isolation and configuration

Use [compose.demo.yaml](../compose.demo.yaml) alone, with project name
`enterprise-rag-demo`. Do not merge it with another Compose file or attach existing
networks/volumes. It publishes only `127.0.0.1:${DEMO_PORT:-18030}:5173`; API (8000),
Qdrant (6333) and Redis (6379) remain private. All five services use a project-scoped
internal backend network, without Internet routing. Only the secret-free frontend
also joins a separate project-scoped edge network, required for effective loopback
port publishing in the verified Docker Desktop runtime. No backend joins edge.
Check the selected host port before
startup; 18030 was free during the infrastructure verification, but availability
must be checked again before subsequent starts.

Project-scoped named volumes are `uploads`, `qdrant_data`, `redis_data`, and
`model_cache`. There are no host-data bind mounts or external volumes. API mounts
`/demo/uploads` read/write; worker mounts it read-only. The image provides an
app-owned mount target (UID/GID 10001); API write access and worker read-only
access passed the first approved infrastructure runtime verification. Model cache is separate from existing
model volumes. Normal shutdown preserves all volumes.

Backend/worker entrypoints validate settings **before importing existing clients**:

| Setting | Required demo value |
| --- | --- |
| `DEMO_ENABLED` | `1` |
| `DEMO_PROVIDER_MODE` | `disabled` (default) or explicitly selected `deterministic` |
| `DEMO_JWT_SECRET` | New demo-only secret, 32–256 printable non-space ASCII characters |
| `DEMO_ACME_PASSWORD_HASH`, `DEMO_GLOBEX_PASSWORD_HASH` | Required by API only; fixed-format scrypt hashes, never plaintext |
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

Upload limits are enforced by the demo upload adapter; disabled provider mode
rejects admission before receiving a body or creating a job. Demo accounts map to ACME/GLOBEX on the server; there is no tenant provisioning.
Redis DB separation and existing cache/job key formats remain unchanged; isolation
comes from the separate Redis instance. Document/job metadata uses the
`demo:v1:` namespace.

No `OPENAI_API_KEY` or existing `JWT_SECRET_KEY` is forwarded. The entrypoint maps
`DEMO_JWT_SECRET` to the existing JWT utility only inside its process. Inherited
provider/JWT credentials and already imported backend clients/config are rejected.
Freshness of a user-supplied secret cannot be proven; never reuse existing secrets.
The frontend receives no secret or upload/model volume.

In disabled mode, authenticated queries return sanitized 503 before query admission/retrieval/cache;
invalid tokens still return 401. Demo startup skips eager RAG/reranker loading.
Process-local disabled SDK clients reject embedding/generation access before any
provider transport, including worker batch embeddings. Opt-in `deterministic` mode
uses local adapters without external inference or model downloads; `openai` and
`ollama` are rejected. Production entrypoints,
provider clients, and existing ingestion behavior are unchanged.

## Static validation (safe without startup)

Use a clearly fake validation value, not an existing secret:

```bash
# Tests supply clearly fake, correctly shaped hashes; no private env files.
python -m unittest tests.test_demo_infrastructure tests.test_demo_sessions -v
python -m compileall -q src tests
git diff --check
```

Tests parse Compose locally (no daemon/startup), check port/network/volume rules,
reject unsafe configuration, and exercise API/worker initialization with fake
clients or blocked sockets. If Docker CLI is absent, Compose-specific tests skip;
configuration parsing is not proof that containers will start successfully.
Never print resolved Compose configuration with real credentials.

## First startup

### Free deterministic localhost demo

Requirements: Git, Docker Engine/Desktop with Compose v2, Python 3.13 for the
credential helper, and sufficient available memory/disk. Node is built inside the
frontend image. The final Linux CI run passed; resource pressure can still prevent
comfortable operation on a busy 8 GB laptop. See [demo configuration](demo.md).

Use these commands for a **fresh installation**. Existing private env files,
containers, collections and volumes must not be overwritten or reset. Check port
18030 and the `enterprise-rag-demo` project before startup. Choose a different
port in a new configuration if needed.

```sh
git clone https://github.com/iremdinc22/enterprise-rag-platform.git
cd enterprise-rag-platform

# Exclusive creation: refuses to overwrite an existing file. No secret is printed.
python3 - <<'PY'
import os, secrets
fd = os.open('.env.demo', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('DEMO_JWT_SECRET=' + secrets.token_urlsafe(48) + '\n')
    stream.write('DEMO_PORT=18030\nDEMO_PROVIDER_MODE=disabled\n')
PY

# Choose your own passwords; interactive input is not echoed.
python3 -m src.demo.passwords --output .env.demo.auth

demo() {
  DEMO_PROVIDER_MODE=deterministic COMPOSE_PARALLEL_LIMIT=1 docker compose \
    --env-file .env.demo --env-file .env.demo.auth -f compose.demo.yaml "$@"
}

demo config --quiet
demo build api
demo build frontend
demo up -d --no-build --wait qdrant redis
demo run --rm --no-deps api python -m src.demo.run init-collection
demo up -d --no-build --wait
demo ps
```

Open **http://127.0.0.1:18030**. Sign in as `acme-demo` or `globex-demo` using the
password you chose for that account. There are no published default passwords.
The private files are ignored by Git; never share or commit them.

Stop only this project while preserving its data:

```sh
demo stop
```

Do not use volume-deletion flags or global cleanup commands. For a pre-existing
installation, reuse its private configuration and follow the [demo guide](demo.md)
instead of rerunning credential generation or copying another corpus.

### Provider configurations

| Configuration | Behavior |
| --- | --- |
| Demo `disabled` — default | Login and existing document access; query/upload return safe 503 |
| Demo `deterministic` — explicit opt-in | Local hash embeddings, lexical retrieval/reranking and source excerpts; no paid inference |
| Original OpenAI backend | Separate normal API/worker configuration with provider credentials and compatible indexed data |

The demo rejects `openai` and `ollama` modes. For the original backend, use the
[deployment guide](deployment.md) and private `OPENAI_API_KEY`/`JWT_SECRET_KEY`
configuration. Its local API commonly uses port 8001; no demo session/upload route
is added to that entrypoint. Provider queries/ingestion may incur charges and must
be invoked intentionally. Never reuse incompatible embeddings in a collection.

The Python helper requires a TTY and never echoes passwords. It stores fixed-format
scrypt hashes with a random 16-byte salt and N=131072/r=8/p=1 in a new mode-0600
file. Single-quoted dotenv values preserve `$` separators. No passwords belong on
command lines; missing/plaintext/unsupported hashes prevent startup. The API alone
receives account hashes. Never overwrite existing private configuration or use
volume-deletion/global-prune commands.

There is no automatic collection seeding, corpus copy or ingestion. `/ready` checks
collection existence rather than provider availability. The dashboard proxy does
not expose Swagger `/docs`. Existing non-demo services and `enterprise_documents`
are outside this project.

## Demo walkthrough

1. Log in to one tenant and upload a small, text-based PDF using the chooser or
   drag-and-drop. Keep its wording available for a question.
2. Wait for the Celery job to reach **Ready**. Failed or uncertain submissions show
   safe feedback; do not repeatedly resubmit an uncertain operation.
3. Ask a question using words present in a source sentence. Read the simulated
   excerpt and click its source card to download the authorized PDF and verify
   the page. Paraphrases and synonyms may produce no evidence.
4. Switch accounts to demonstrate separate libraries and evidence. The frontend
   clears session-owned documents, queries, polling and download state.

For a predictable first test, create a PDF sentence such as “Orion allowance is
731 USD.” and ask “What is the Orion allowance?” The example is user-created
synthetic content, not a bundled corpus or a hardcoded answer. Public query
responses do not expose cache HIT/MISS, token usage or cost metrics.

## Demo session API

`POST /api/v1/demo/session` exists only in the activated demo application.
The normal `src.api.main:app` does not expose it, even if demo environment variables
are present. Demo mode must be explicitly enabled, with a distinct JWT secret.

Request (password placeholder is illustrative, not a supplied credential):

```json
{"username": "acme-demo", "password": "<entered locally>"}
```

Only `username` (1–64 characters) and `password` (1–256 characters) are accepted.
No stripping or case conversion occurs. Extra fields and query parameters return
sanitized 422; client tenant/role/claims never determine the token. The fixed mapping
is `acme-demo -> acme/admin`, `globex-demo -> globex/admin`. Both accounts can query
and authorize demo uploads when a provider is enabled; there is no external identity
provider or signup.

Success 200 contains `access_token`, `token_type: "bearer"`, `expires_in: 900`, and
`user: {user_id, tenant_id, role}`. Existing JWT helpers sign and verify HS256 with
15-minute expiration. The dashboard keeps the token in memory; never
place it in URLs, logs, browser bundles or localStorage. All session-path responses,
including errors and redirects, carry `Cache-Control: no-store`.

Invalid account/password returns the same 401 `Invalid credentials`. Unknown
accounts still perform one password derivation. Verification uses constant-time
digest comparison. Backend exceptions return sanitized 503 without exception text.

Session protection precedes JSON parsing: 4096 actual body bytes (413), five seconds
total body reception (408), malformed Content-Length (400). Missing/chunked or
underdeclared lengths are still byte-counted. These limits are separate from query
body protection; existing query defaults are unchanged.

A process-local lock admits at most 10 POST attempts in a rolling 60-second window.
Attempts include success, invalid credentials, invalid bodies, timeouts and busy
rejections; none are refunded. Rate rejections return 429 plus `Retry-After` and
start no password verification. There is one active scrypt operation and no waiting
queue; busy rejection is also 429. Each derivation needs about 128 MiB, bounded by
a 256 MiB OpenSSL memory ceiling. Hashing uses the existing cancellation-draining
execution helper in a thread: cancellation retains the slot until work finishes.
Health and query quotas are independent. The five-second timeout is for receiving
the body, not a hard cutoff for password hashing or response delivery.

## Historical infrastructure and authentication checkpoints

The infrastructure runtime passed five-service health checks, localhost placeholder
access, demo-only DNS/network/volume checks, empty demo collection readiness,
Redis connectivity, worker ping, and disabled-query 503. The stack was then stopped
with its volumes retained. Two startup fixes were required: invoking the placeholder
module main, and adding a frontend-only edge network for effective localhost port
publishing. No existing database contents were accessed.

The final TASK-030.4 checkpoint also passed real HTTP authentication inside the
isolated API container, without publishing its port: ACME/GLOBEX login 200 with
correct tenant/admin claims and 900-second JWTs; invalid credentials/unknown account
401; missing fields and extra claims 422; authenticated query 503 with provider
disabled; unauthenticated query 401; eleventh login POST 429. Session responses
carried no-store. The existing normal API returned 404 for the session route, with
no credentials sent to it. All five demo services were healthy and the frontend
placeholder returned 200 on localhost.

The interactive helper was exercised through a terminal with echo disabled, using
fresh random verification-only passwords kept in memory. Only salted hashes were
retained in the ignored mode-0600 `.env.demo.auth`; passwords and tokens were not
printed or retained. For later manual login, generate a new ignored auth file under
a different name with operator-chosen passwords and select it as the second
`--env-file`; do not overwrite existing private files.

After verification only the demo stack was stopped, retaining all four demo volumes.
Read-only before/after hashes confirmed the existing 23 Qdrant points, local Redis
values and existing private env files were unchanged. Existing RAG container
identities/start times/restart counts/mounts/networks were unchanged. Two unrelated
SQL containers showed metadata changes during the checkpoint; no commands targeted
them. No documents were ingested and no provider calls were made.

Rate state is per process and resets on restart; it is not a distributed auth
service. Token refresh, revocation and account management are not implemented.
Passwords require operator setup; there are no default passwords. The frontend
now uses a same-origin nginx proxy and serves source PDF downloads. Inline PDF
viewing is not implemented. Upload/job adapters exist
with admission enabled only in explicitly selected deterministic mode.
See [the deterministic demo guide](demo-deterministic.md) for manual startup.
No real provider calls, public deployment or hard billing-cap mechanism is supplied.
