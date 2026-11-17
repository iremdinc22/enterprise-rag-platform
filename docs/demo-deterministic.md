# Deterministic localhost RAG demo

[Demo setup](demo.md) · [Dashboard](demo-dashboard.md) · [Upload contracts](demo-uploads.md)

`DEMO_PROVIDER_MODE=deterministic` explicitly enables a zero-provider-cost demo.
`disabled` remains the Compose default; `openai` and other modes are rejected.
This mode is available only through the validated demo entrypoint and the isolated
`enterprise-rag-demo` project. It never enables a production provider.

## What runs

The existing parser, chunker, tenant-owned upload metadata, Celery ingestion task,
Qdrant ownership/preparation checks, BM25/vector retrieval, reciprocal rank fusion,
deduplication, MMR, citation builder/validation, Redis caches and query API run
normally. There is no separate fixture-backed API or hardcoded answer dictionary.
Only provider/model boundaries change inside the demo process:

- `demo-hash-v1` embeddings: SHA-256 feature hashing over case-folded word tokens,
  signed counts, L2 normalization, **1536 dimensions**. A fixed nonzero fallback
  handles punctuation-only inputs. The API checks the real collection dimension
  at startup; ingestion also uses the existing pre-deletion dimension checks.
  No collection is migrated, reset or automatically re-indexed.
- Cross-Encoder replacement: deterministic lexical overlap scores with point-ID
  tie breaking. No Hugging Face model is loaded or downloaded. Production
  Cross-Encoder code is unchanged. Existing BM25/RRF/MMR still execute; a demo-only
  empty-corpus guard prevents the existing BM25 constructor's empty-index error.
  Nonempty lexical retrieval uses the existing implementation (with one additional
  bounded tenant-scoped read for that guard).
- `demo-extractive-v1` generation: choose an actual retrieved sentence containing
  every non-stopword query term, then quote it with the trusted group's citation
  ID. The adapter ignores formatted prompt text as an authority and never uses
  filenames or embedded `[SOURCE ...]` markers to authorize IDs. Sentences with
  bracket markers are skipped. Excerpts are bounded to 500 characters/80 words.
  Existing structured citation validation and final API citation validation run.
- If no qualifying evidence exists, return the existing exact abstention response
  with an empty citation list. Empty tenants also abstain safely.

The generation adapter performs at most one local attempt; it has no network
transport or fallback. OpenAI sync/Azure/unknown operations remain blocked;
only the existing async embedding interface accepts the local hash model. Query
provider timeout/deadline parameters remain compatible, and admission, body,
rate/quota, authentication and tenant protections are unchanged. Ingestion keeps
its existing bounded Celery retry and metadata semantics.

The dashboard reads `X-Demo-Provider-Mode` from same-origin `/health` and displays
**Simulated AI — deterministic local demo**. Query/session JSON schemas are
unchanged. No fake cache HIT/MISS, provider token counts or cost metrics are shown.
Local adapters return no fabricated provider usage. Existing internal span names
are reused; they should not be interpreted as paid API-call counters in this mode.

## Limitations

This is lexical feature hashing and extractive matching, not an LLM or semantic
embedding model. It does not reason, paraphrase, calculate, resolve synonyms, or
prove real LLM quality. Relevant evidence can be missed if wording differs, spans
chunks, contains bracket markers, or is longer than the excerpt limits. Existing
BM25 corpus loading remains bounded to its first 100 tenant points. Hash collisions
are possible. Change model/version names and plan compatibility before changing
embedding semantics; do not silently mix vector formats in this collection.

Document replacement is not atomic. Existing partial-write, concurrent-ingestion
and cache consistency limitations remain. Demo uploads generate new document IDs;
retries refuse to overwrite already indexed documents, including ambiguous partial
writes. Failures can leave source files or partial points for operator review.
Demo quotas persist in demo Redis: ten accepted uploads per tenant, twenty globally;
query rate/quota state is process-local. No volume reset is needed or recommended.

## Manual frontend testing

Run from the repository root. Docker Desktop must already be running. The only
published port remains `127.0.0.1:18030`. Do not merge Compose files, use existing
non-demo services, or expose backend ports.

Create a **new** ignored password-hash file with passwords you choose:

```sh
# Run from the repository root.
.venv/bin/python -m src.demo.passwords --output .env.demo.manual.auth
```

The helper prompts privately for both passwords and confirmations, creates mode
0600 hashes, and refuses to overwrite an existing file. If that filename exists,
choose another new filename and use it consistently below. No default password is
provided or embedded anywhere. Accounts are `acme-demo` and `globex-demo`; each maps
server-side to its own admin tenant. Existing `.env.demo` supplies the distinct demo
JWT secret; never replace it with the normal API signing secret. If this is a fresh
checkout without `.env.demo`, follow the safe first-time secret setup in [demo.md](demo.md)
first, without overwriting an existing private file.

Use an explicit command-local mode override, leaving environment files unchanged:

```sh
demo() {
  DEMO_PROVIDER_MODE=deterministic docker compose \
    --env-file .env.demo --env-file .env.demo.manual.auth \
    -f compose.demo.yaml "$@"
}

demo config --quiet
demo build
# Start only demo dependencies, then explicitly/idempotently ensure the collection:
demo up -d --wait qdrant redis
demo run --rm --no-deps api python -m src.demo.run init-collection
demo up -d --wait
demo ps
```

Open **http://127.0.0.1:18030**. Log in with the account and password you selected.
Check the simulation banner, upload a small text PDF, wait for Ready, ask a question
using words present in the source, and download its cited source PDF/page. Switch
accounts and verify the other tenant's library and answers remain separate. JWTs
stay in memory and expire in 15 minutes; source downloads use authenticated fetch.

Stop only this project while keeping its data:

```sh
demo stop
```

Never use `down -v`. Returning to disabled mode requires a demo-only restart with
`DEMO_PROVIDER_MODE=disabled`; existing documents remain available for listing and
PDF download, while new ingestion/queries reject with safe 503 before provider work.
No mode permits real OpenAI or other external inference calls.

## Automated verification

Network-free regression commands:

```sh
.venv/bin/python -m unittest discover -s tests -q
# The legacy evaluation imports SDK/model constructors; block networking and
# mock those constructors so neither real credentials nor cached models are needed.
PYTHON_DOTENV_DISABLED=1 OPENAI_API_KEY=offline-evaluation-placeholder \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python - <<'PYTEST'
import unittest
from unittest.mock import patch
with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")), \
     patch("qdrant_client.AsyncQdrantClient"), patch("sentence_transformers.CrossEncoder"):
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.discover("evaluation/tests", pattern="test_gener*.py"),
        unittest.defaultTestLoader.discover("evaluation/tests", pattern="test_retrieval_metrics.py"),
    ])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
PYTEST
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

`tests.demo_e2e` is an explicitly invoked HTTP test runner. It refuses non-deterministic
health metadata before uploading and targets only localhost:18030. It creates two
synthetic two-page PDFs through the real upload endpoint, polls bounded job status,
checks tenant facts/citations/download bytes, replay/conflict behavior, malformed
PDF rejection and cross-tenant job/PDF denial. It has no signing secret or provider
key and does not start/stop Docker. Run only against an approved isolated stack:

```sh
.venv/bin/python -m tests.demo_e2e --execute-demo-e2e \
  --credentials-file /private/tmp/your-private-demo-credentials.json \
  --report-file /private/tmp/your-new-demo-report.json
```

The credentials file must be temporary mode 0600 JSON with exactly `acme-demo`
and `globex-demo` keys. Never put it in Git or pass passwords on the command line;
remove it after testing. The report contains only safe check names and synthetic
IDs/provenance. Each run consumes two real demo upload reservations and retains
those synthetic documents for manual exploration; do not run it in an unrestricted
loop. The provider-disabled profile is verified separately, without accepting jobs.

## Verified checkpoint — TASK-030.8B

The initial real Docker run began with zero demo points/documents/jobs and an
empty model cache. Twelve grouped HTTP integration checks passed through the real
frontend proxy. Two uploaded PDFs reached Celery `completed` on attempt zero and
produced four page chunks; the browser then uploaded a third PDF through the real
UI, observed Ready via polling, queried it and downloaded its citation PDF with
matching SHA-256. The following deliberately synthetic documents remain in the
demo volumes for manual testing:

| Tenant | Source PDF | Example question | Verified value/page |
| --- | --- | --- | --- |
| ACME | `SYNTHETIC-acme-orion.pdf` | What is the Orion allowance? | 731 USD, page 2 |
| ACME | `SYNTHETIC-acme-beacon.pdf` | What is the Beacon allowance? | 419 USD, page 2 |
| GLOBEX | `SYNTHETIC-globex-nebula.pdf` | What is the Nebula allowance? | 284 USD, page 2 |

Foreign job/PDF access matched nonexistent-resource 404s. Foreign unique-fact
queries abstained with no citations. Same-file idempotent replay returned the
original IDs; changed-file replay returned 409 and corrupt PDF input returned 422.
Empty tenants abstained before ingestion, and post-ingestion queries returned the
new facts (including invalidation of the previously cached abstention). A cached
repeat preserved correct citations; the public API/UI does not expose HIT/MISS.

The runtime was resumed after an external Docker shutdown without repeating any
upload: all three documents/jobs and six 1536-dimensional points persisted. A
fresh GLOBEX browser query returned 284 USD with its page-2 source; account switching
cleared the prior answer. Returning only demo services to disabled mode produced
503 for both tenants' query/upload requests and the correct disabled UI. Exact
Redis document/job/submission/quota hashes remained unchanged; there were still
three completed jobs, no active job/broker queue, six points and no model-cache
files. No OpenAI/external inference or model download was used.

Regression verification: 274 backend tests + 20 frontend tests + 30 generation/
retrieval evaluation tests = 324 passes. Typecheck, production build, compilation,
Compose parsing, link and secret/artifact checks are separate validations. The
older TASK-030.8A documentation was retained. This checkpoint proves the local
integration and provenance path, not real LLM answer quality.

Final state: the five demo services are stopped; all four named volumes and the
three synthetic documents are retained. Eight ACME and nine GLOBEX upload
reservations remain (seventeen globally), subject to the existing fixed demo
quotas. Temporary verification credentials were removed. Use the interactive
password helper above for your own manual login; there are no reusable test
passwords. Six existing non-demo RAG containers and the four original private env
files were unchanged during the resumed verification. Some unrelated containers
changed metadata independently; none was targeted by task commands. TASK-030.9
was not started, and no commit/push was performed.

## Final release validation

The checkpoints above record earlier development stages. See
[final validation](final-validation.md) for the subsequent clean Linux installation,
HTTP controls and authenticated browser evidence.
