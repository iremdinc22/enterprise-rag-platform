# Atlas demo dashboard

[Demo setup](demo.md) · [Upload and source access](demo-uploads.md)

The frontend is React + TypeScript + Vite, with native fetch, Lucide icons,
Vitest and Testing Library. No frontend secrets or provider integrations exist.
System fonts and bundled icons avoid external asset requests.

The opt-in [deterministic local RAG mode](demo-deterministic.md) now supports the
full upload-to-citation workflow without an LLM or external inference provider.
Disabled remains the default; the checkpoint below records the earlier disabled-mode verification.

## Development and verification

Use Node 22.17+ (or another supported Node release for the locked Vite version):

```sh
cd frontend
npm ci
npm run typecheck
npm test
npm run build
npm run dev
```

Open the printed localhost URL. Standalone Vite is intentionally **UI-only**:
there is no fallback to localhost:8001 or any existing API. It displays the login
screen, but successful authentication requires the approved isolated demo stack.
Tests mock fetch; there is no browser-accessible fake account bypass or simulated
provider mode. Tokens, documents, answers and passwords are never saved to local
storage, session storage or URLs. Reloading requires login again.

## Approved isolated startup

After separate runtime approval and the setup/collection initialization in
[the demo guide](demo.md), rebuild the frontend and use the isolated project:

```sh
docker compose --env-file .env.demo --env-file .env.demo.auth -f compose.demo.yaml build frontend
docker compose --env-file .env.demo --env-file .env.demo.auth -f compose.demo.yaml up -d
docker compose --env-file .env.demo --env-file .env.demo.auth -f compose.demo.yaml ps
# Open http://127.0.0.1:18030
# Stop only the demo project; preserve its volumes:
docker compose --env-file .env.demo --env-file .env.demo.auth -f compose.demo.yaml stop
```

The checkpoint below records the completed disabled-provider runtime verification.
Future startup still requires explicit approval. Do not merge Compose files, expose backend ports, or overwrite
existing credentials. The demo frontend has its own multistage build and runs nginx
as a non-root user on 5173. Only the frontend publishes a loopback port. The nginx
proxy routes `/api/v1/`, `/health`, and `/ready` to the internal demo API; there are
no browser-configurable API hosts or tenant parameters. Access logging is disabled;
static assets have a restrictive same-origin CSP. Frontend container health uses
API liveness, not model/provider readiness.

## Interaction contracts

- Select ACME or GLOBEX and enter operator-configured credentials. Session claims
  returned by the server identify the workspace. Client selection is only an
  account name, never an authorization decision. Invalid credentials are generic;
  401 or the 15-minute expiry returns to a cleared login screen.
- Documents are paginated 20 at a time, with loading, empty, failed and ready
  states. Refresh is manual except when ingestion completes. File size is checked
  locally (5 MiB); server-side PDF/page/chunk/text checks remain authoritative.
- Upload sends one PDF and a generated UUID Idempotency-Key. The same file digest
  reuses its key within the current session, including manual resubmission after
  uncertain publication. There are no automatic upload/query retries. Unknown
  publish outcomes with a job ID are tracked instead of silently duplicated.
  Keys are not durable across reload/sign-out; check existing submissions before
  manually resubmitting after either event.
- Job polling waits two seconds between non-overlapping cycles. Terminal jobs
  stop polling; errors stop that job's polling and show a safe message. Session
  unmount aborts requests and clears timers. A later document refresh can resume
  tracking a nonterminal job. UI navigation between Documents and Ask AI preserves
  tracking within the same session.
- Ask AI limits input to 2,000 characters, allows one in-flight question, and uses
  the existing backend retrieval defaults and cache-enabled flag. Answers are
  rendered as plain text, not raw HTML. Structured sources show filenames and
  page numbers; clicking fetches the authorized PDF and downloads it. No inline
  PDF viewer or direct token-bearing links are used.
- Session-keyed state, abort signals and late-response checks prevent old-tenant
  data appearing after account switching. Blob URLs are revoked on sign-out and
  shortly after download. Cancellation cannot undo an already accepted server
  operation or provider billing.
- Provider mode remains disabled. A recognized disabled-provider 503 explains the
  limitation and disables repeated provider actions. No fake answer, ingestion
  success, cache HIT/MISS, token count or cost metrics are displayed. API liveness
  is a one-time `/health` check; it does not claim every dependency is healthy.

## Verification and limits

Component/API-client tests cover login, safe errors, upload validation and
idempotency, polling termination, citations, authenticated PDF fetch, quota
feedback, expiry and late-response isolation. Backend suites remain authoritative
for JWT, ownership and quotas. Production build/typecheck, static Compose parsing,
secret/artifact checks and an npm advisory audit are separate checks.

Login layout was visually checked at mobile and desktop widths. The authenticated
dashboard interactions are covered by mocked component tests and the disabled-provider
Docker/browser checkpoint below. Provider-dependent success paths remain deferred. No existing containers were started and no
paid calls or data changes were needed. Demo provider activation is out of scope.
This is a localhost portfolio demo, not a public deployment or a hard billing cap.

## Docker/browser checkpoint — 2026-11-17 (TASK-030.8A)

The dashboard revision committed as `83b8a4245e915d0438a982cf3d95f00110052916`
was built with the backend and started only as `enterprise-rag-demo`. All five
services were healthy. nginx configuration validation passed; HTML, JS, CSS,
`/health` and `/ready` returned 200 through port 18030. Actual container mounts,
networks and port bindings matched the isolated configuration; backend services
had no published ports and no OpenAI credential was supplied.

Real browser ACME and GLOBEX logins succeeded. Both initial libraries were empty.
Account switching returned to a cleared login form and cleared prior question,
provider-error and document state. Authenticated desktop (1440×960) and mobile
(390-pixel wide) layouts were visually checked. Browser console inspection
reported no captured application errors/warnings. Expected 401/404/503 responses
were checked separately over HTTP; they are not successful provider operations.

One explicitly named synthetic ACME PDF and its isolated document metadata were
created solely to test source access. The metadata deliberately used `failed`
status; no ingestion job, worker task or Qdrant point was created, and this was
not evidence of a failed or successful ingestion run. ACME listed and downloaded
it; browser download SHA-256 matched the fixture. GLOBEX still had an empty list,
and foreign-document versus missing-document access returned identical 404s.
The file, exact metadata key and downloaded test copy were removed after checking
their identity/hash. No existing corpus document was used as a fixture.

Browser query and upload attempts showed sanitized provider-disabled errors and
disabled further provider-dependent actions within that session. HTTP checks
confirmed 503. Demo job keys, active jobs, upload quota entries and broker queue
remained empty; demo Qdrant point count stayed zero. Signed expired JWT access
was rejected with 401 through nginx/API. The real 15-minute idle browser timer
was not waited out: UI expiry/401 clearing remains covered by the fake-clock
component regression tests, and session responses retained `expires_in=900`.

No application correction was required. Temporary HTTP assertions were updated
for repeated `Cache-Control`/`X-Content-Type-Options` headers added by nginx and
the API; their parsed values still contained `no-store`/`nosniff` respectively.
No secrets or response payloads were included in diagnostic output.

Host verification repeated 19 frontend, 264 backend, 22 generation and 8 retrieval
tests (313 passes), TypeScript/production build, compilation and all three Compose
configuration checks. Existing six non-demo RAG containers retained their IDs,
start times, restart counts, mounts and networks; the four existing private env
files retained their SHA-256 hashes. Some unrelated containers changed metadata
independently; no commands targeted them. Existing Qdrant/Redis records were not
read or written by the runtime tests.

After verification only the five demo services were stopped, preserving all four
demo volumes and the project networks. Temporary runtime credentials were removed;
existing credential files were not overwritten. Future startup still requires
operator-configured passwords as described above. Real ingestion, generated
answers, citation retrieval and cache behavior remain untested in this disabled
runtime; they require the separately approved deterministic-provider phase.
