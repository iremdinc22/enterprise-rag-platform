# Testing and verification

[Overview](../README.md) · [Architecture](architecture.md) · [API](api.md) · [Deployment](deployment.md)

## Verification layers

Host unit/regression tests use mocks. Deterministic integration tests run real
HTTP, Qdrant, Redis, Celery and PDF processing with fake model boundaries. The
separately approved live smoke test uses the real existing ACME/GLOBEX corpus and
OpenAI under an isolated budget guard. These layers make different claims;
passing infrastructure tests alone does not demonstrate answer quality.

### Network-free regressions

Run from the repository root in the configured virtual environment:

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s evaluation/tests -p 'test_gener*.py' -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest discover -s evaluation/tests -p 'test_retrieval_metrics.py' -v
python -m compileall -q src tests
sh -n scripts/test-e2e.sh
git diff --check
```

The host suites cover 12 API/configuration tests, 39 live-guard/network/diagnostic
tests, 22 generation tests and 8 retrieval-metric tests. Retrieval-metric imports
currently load the existing Cross-Encoder: its weights must already be cached
for offline execution. A sandbox Qdrant compatibility-check warning has previously
been non-fatal. Do not substitute broad live retrieval or ingestion suites:
those can invoke paid embeddings. The four existing live tenant-isolation tests
passed during API verification; they are not part of a no-spend regression run.

## Deterministic integration tests

```bash
./scripts/test-e2e.sh
```

Requires Docker Compose and an image build/download connection. The helper uses
[compose.e2e.yaml](../compose.e2e.yaml) as a standalone file, never as an override
of the deployment stack. It chooses a unique `rag-e2e-*` project, ignores private
env files, publishes no ports, uses an internal-only network, and mounts no host
data or persistent volumes. Qdrant/Redis storage is tmpfs; Redis persistence is
disabled. A temporary JWT signing secret is generated inside the test process.
The helper checks the actual network, mounts and port bindings before testing.
Its exit trap removes only its project containers/network; image build caches
remain. No global prune or persistent-volume deletion is used.

The test bootstrap blocks real SDK factories and restricts sockets/DNS to the
isolated services/loopback. Deterministic 1536-dimensional embeddings, a fake
structured generator and a lightweight reranker replace models. Real pipeline
code still handles BM25/vector fusion, deduplication, MMR, citation validation,
usage accounting and caching. Synthetic two-page tenant PDFs are generated in
temporary container storage; existing source PDFs and collections are not used.

| Scenario group | Coverage |
| --- | --- |
| Authentication and validation | Invalid/missing tokens, schemas, tenant override attempts |
| Retrieval isolation | ACME/GLOBEX evidence and citations through the real hybrid pipeline |
| Cache | Tenant-specific MISS/HIT/bypass and re-ingestion invalidation |
| Concurrent requests | Request-local usage accounting |
| Citation validation | Successful retry and retry exhaustion |
| Ingestion | PDF pages/chunks, 1536-dimensional vectors, job transitions |
| Job failures | Missing file and transient embedding failure with real Celery retry |
| Re-ingestion | Valid replacement and answer invalidation; empty/missing/malformed/wrong-dimension candidates and schema-read failures preserve existing points/cache with zero delete/upsert/invalidation calls |
| Qdrant faults | Connection/timeout paths and readiness 503 |
| Redis faults | Cache get/set failures and fail-open behavior |
| Generation faults | Safe HTTP error behavior |
| Empty data/context | Existing safe HTTP 500 behavior |
| Metadata validation | Invalid page metadata rejection and test-owned restoration |
| Network policy | Public DNS and literal-IP denial |

The service-integration fixture explicitly uses higher test-only rate/quota limits
(1000) to run all scenarios in one application. Default admission policy is tested
separately with a fake monotonic clock in `tests/test_query_admission.py`; production
defaults are unchanged. Those network-free tests cover rolling tenant/global limits,
lifetime accounting, atomic concurrent admission, no refunds, and rejection before
pipeline work.

There are **18 scenarios**. The test worker uses one task per child to isolate
existing process-global async client lifecycles; this is a test-only setting, not
a production fix. Cleanup is limited to owned collection, job IDs and exact cache
keys; it never flushes Redis or touches other projects.

The suite passed all 18 scenarios twice during initial finalization. Earlier runs
also exposed intermittent Qdrant/background-job and HTTP timeout failures; fresh
unchanged runs passed. The helper does not automatically retry failures. Faults are injected at client
calls; actual network partitions, failover, broker outage recovery, mid-ingestion
crashes, retry exhaustion and concurrent document replacement remain untested.
Repeated tasks in a long-lived production worker child are also unverified. This is
functional coverage, not a load test, penetration test or uptime guarantee.

## Pre-deletion ingestion safety verification

The final TASK-029.3B run passed 193 host regressions (163 main, 22 generation,
8 retrieval metrics), the same 193 regressions inside the Docker image, and all
18 isolated E2E scenarios. The replacement scenario includes five preparation
fault subtests and verifies unchanged stored points/cache plus zero
delete/upsert/invalidation calls. These subtests are included in the 18 scenarios,
not counted as additional scenarios.

The first E2E run overlapped Docker regressions: a concurrent query hit the HTTP
client timeout, followed by capacity rejections (four failures and one error).
Running the unchanged suite sequentially passed all 18. This demonstrates timing
sensitivity under concurrent test load; it is not evidence of atomic replacement
or a production load guarantee. Qdrant writes after deletion, concurrent ingestion
races, and best-effort cache invalidation remain outside the preparation guarantee.

## Controlled live smoke test

### Verified result

The final explicitly approved execution passed exactly three HTTP requests using
the existing local development corpus:

| Request | HTTP | Cache | Evidence | Citation |
| --- | --- | --- | --- | --- |
| ACME: remote work allowance | 200 | MISS | 500 USD | `acme-policy.pdf` |
| GLOBEX: remote work allowance | 200 | MISS | 2000 USD | `globex-policy.pdf` |
| ACME repeat | 200 | PINNED_HIT | 500 USD | `acme-policy.pdf` |

No cross-tenant citations were returned. Both policy citations identified document
`remote-work-policy`; tenant-specific filenames and authenticated tenant scopes
distinguished them. Full source text, answers,
JWTs and credentials are intentionally not recorded here.

| Measurement | Observed total |
| --- | --- |
| Embedding provider calls | 3 |
| Generation provider calls | 4, including two permitted citation-validation retries |
| Free input-token-count calls | 4 |
| Embedding input tokens | 278 |
| Generation input / output tokens | 2327 / 192 |
| Estimated provider cost | USD 0.00124356 |
| Retained pre-call reservation | USD 0.00576920 |
| Provider calls from third request | 0 |

These are observed usage and local price estimates, not an invoice or account-wide
billing audit. The retained reservation is deliberately larger than observed cost
and is not refunded. The successful run made 7 paid calls and 11 provider requests
in total. Both uncached answers used one citation-validation retry. These results
do not settle billing for earlier ambiguous attempts. No additional live calls
are needed to verify documentation.

### Enforcement mechanism

The [runner](../tests/live_smoke/run.py) launches an isolated instrumented process
using the existing FastAPI factory and `run_rag()`. It does not submit requests to
the already running development API. Its loopback Uvicorn server uses an ephemeral
port. Before application imports it installs a shared guarded OpenAI client and
narrow service/network adapters; production defaults are unchanged.

The only allowed POST bodies are the predefined ACME, GLOBEX and repeated ACME
question, in order: “What is the remote work allowance?”, retrieval limit 5, final
limit 3, lambda 0.5, cache enabled. Middleware validates request sequence/body and
private, in-memory tenant tokens. Any failure aborts without replacement queries.

The runner verifies model values `gpt-4.1-mini` and `text-embedding-3-small`, local
Qdrant `localhost:6333`, collection `enterprise_documents`, Redis cache
`localhost:6381/2`, and the supported OpenAI base URL. It requires existing cached
reranker weights and refuses a previously imported production pipeline.

At the actual SDK HTTP transport boundary:

- SDK retries and transport retries are zero; redirects are disabled, timeout is
  30 seconds and environment proxies are ignored.
- Only supported serialized embedding, Responses and token-count requests pass.
  Unsupported models, paths, payload forms, streaming, tools or fallbacks fail closed.
- Each generation has `max_output_tokens=500`, `store=false`, disabled truncation
  and the default service tier. At most two attempts per uncached query are allowed,
  including the one existing citation-validation retry.
- Exact complete generation input is counted before the paid request, including
  instructions and structured-output formatting. The maximum is 4096 input tokens.
  Unknown/inconsistent usage, incomplete responses and unsupported response behavior abort.
- Embedding inputs are bounded to a single query or a batch of at most five strings,
  each at most 2048 UTF-8 bytes. Each string reserves the full supported 8192-token
  input capacity, rather than trusting a token-count heuristic.
- A Decimal ledger reserves conservative input cost plus the full 500-token output
  allowance **before every paid call**. Insufficient budget blocks dispatch.
  Reservations are never released, including on failure, timeout or ambiguous billing.
- Provider ceilings are four embedding calls, four generation calls and four free
  token-count calls across the run. No provider call is permitted in phase three.

At the runner's assumed prices (USD 0.02/million embedding input tokens,
USD 0.40/million generation input tokens, USD 1.60/million generation output), the
maximum allowed reservation is **USD 0.01171968**, below the USD 0.10 local budget:

```text
Embeddings: 2 × (1 + 5) × 8192 × 0.02 / 1,000,000 = 0.00196608
Generation: 4 × (4096 × 0.40 + 500 × 1.60) / 1,000,000 = 0.00975360
Total:                                                     0.01171968
```

This is a pre-call bound within this instrumented runtime under its supported
provider contract and price assumptions. It is not an organization billing cap:
other clients using the key, taxes, pricing changes or provider contract changes
are outside the local ledger. Normal production usage tracking is after-the-fact
and does not implement this guard.

### Corpus, network and cache safeguards

Qdrant is exposed through a read-only facade allowing collection existence,
scrolling and vector queries; mutation methods are unavailable. No ingestion runs.
Redis access uses a unique smoke namespace, exact registered keys, and cleanup
limited to those keys; production cache/job keys are untouched. It never flushes
Redis. Failed cleanup may leave only owned expiring keys until their TTLs expire.

After the first ACME answer, the runner validates its namespaced Redis value,
TTL of at least 30 seconds, schema, tenant-specific policy evidence, and equality
with the first response, then pins a deep copy. The third request uses that
snapshot through the pipeline's cache path, with every provider boundary blocked.
`PINNED_HIT` therefore proves reuse of a verified cached answer; it does not prove
a fresh Redis read or resilience to TTL expiry during the third request.

Public provider DNS/connect access is allowed only in the active guarded send
scope; other tasks remain blocked. Byte hostnames are safely normalized for
allowlist comparison. Async DNS executor calls explicitly propagate the calling
context so the scoped policy remains active without globally allowing egress.
Read-only local service access is separately permitted.

### Safe diagnostics and earlier failures

Two earlier attempts aborted on the first ACME request due to smoke-runtime
network-policy defects, not established provider authentication/quota failures:

1. Rejected byte hostname `b"localhost"`; zero provider calls or reservations.
2. Lost DNS policy context in the asyncio executor; one embedding attempt,
   no validated usage, retained USD 0.00016384 reservation. Actual billing for that
   ambiguous attempt was not independently established and must not be inferred.

Both were corrected in the isolated runtime with network-free regressions. The
production HTTP 500 handler intentionally returns a generic safe message.
Sanitized smoke diagnostics distinguish guard/DNS/TLS/timeout/response-validation
failures and provider HTTP classes. They expose only exception class, failure
stage, HTTP status when known, an allowlisted category and sanitized source
location. They never print exception bodies, headers, prompts or credentials.
A 429 category cannot distinguish rate limits from quota without inspecting a
body, which this runner deliberately avoids. Earlier response status is reset
before a new provider send so diagnostics cannot inherit a successful count status.

### Running a later live test

A new execution requires separate explicit approval. The inert command performs
no provider work:

```bash
python -m tests.live_smoke.run
```

**Only after approval**, in a fresh process with the verified local corpus,
services, models and private credentials already configured:

```bash
python -m tests.live_smoke.run --execute-approved-live-smoke
```

Run exactly once, inspect only the sanitized status/citation/usage/count/budget
summary, and stop on failure. Do not retry ambiguous paid requests or make debug
provider calls. Tokens are minted only in memory as needed; do not capture them.
The runner does not automatically persist a results artifact. No env file,
existing document, collection or deployment default should be changed to run it.

Network-free tests cover output caps, disabled retries, citation retry ceilings,
pre-call reservations, insufficient funds, retained failed-call reservations,
provider counts, third-request denial, unsupported calls, read-only adapters,
byte-hostname handling, task-local DNS context and sanitized diagnostics.

## Evaluation and benchmarking

Existing evaluation scripts are separate from the isolated test harness. They
can make paid calls and overwrite reports; do not run them as ordinary no-spend
regressions. Historical saved measurements below are small development baselines,
not production guarantees or a new evaluation of the complete corpus.

| Saved baseline | Result | Scope |
| --- | --- | --- |
| [Retrieval](../evaluation/results/retrieval_baseline.json) | Precision@3 0.3611; Recall@3 1.0000; MRR 0.9375 | 24 cases, October 8, 2026 |
| [Ablation](../evaluation/results/ablation_results.json) | Hybrid + dedup MRR 0.9583; + reranker 0.9306; + MMR 0.9375 | Same 24 cases; no measured ranking improvement |
| [Generation](../evaluation/results/generation_baseline.json) | 8 PASS, 0 FAIL, 0 ERROR | October 9, 2026; phrase/citation/document and abstention checks |
| [Performance](../evaluation/results/performance_baseline.json) | See table below | Sequential two-question cache comparison, October 11, 2026 |

Retrieval precision uses a fixed denominator of 3; recall excludes cases without
labels from the mean. Generation checks citation consistency and expected phrases,
not semantic faithfulness. MMR diversity was not independently evaluated.

| Cache status | Requests | Mean / p50 / p95 | Tokens | Estimated USD |
| --- | --- | --- | --- | --- |
| BYPASSED | 2 | 4071.01 / 4071.01 / 5035.83 ms | 2790 | 0.00122048 |
| HIT | 4 | 5.90 / 5.50 / 9.23 ms | 0 | 0 |

The two bypassed requests made four generation attempts including citation retry.
An earlier four-request bypass sample measured mean 2780.38 ms, p50 2558.99 ms,
p95 4003.37 ms, 4096 tokens and USD 0.00174188. These tiny sequential samples are
not throughput results or latency SLOs. `use_cache=false` bypasses answer caching
only; query embedding caches may still hit. Warmup can also create HITs. Timing
uses `perf_counter`, actual returned cache state and in-memory spans; avoid enabling
the console tracing exporter at the same time as the performance harness.

Explicit paid evaluation entry points are `evaluation.retrieval_runner`,
`evaluation.ablation_runner`, `evaluation.generation_runner` and
`evaluation.performance_runner` (supports `--limit` and `--compare-cache`). Review
their target services, cost and report outputs before any separately approved run.

## Documentation finalization verification

On November 16, 2026, the finalization rerun passed **99 checks/tests**:

- 51 API/configuration and smoke guard/network/diagnostic tests.
- 22 generation regression tests.
- 8 retrieval-metric tests using cached model weights in offline mode.
- 18 deterministic E2E scenarios in a fresh isolated Docker project.

Both Compose configurations passed quiet validation. Python compilation, POSIX
helper syntax, documentation shell/JSON syntax, relative links/anchors and
`git diff --check` passed. The isolated project was cleaned up after the run.
No paid provider test, ingestion of the existing corpus or environment-file
change was performed for documentation finalization. Earlier live success is
recorded above; it was not repeated.

## Remaining limitations

The live smoke result covers three predefined requests and two policies, not
all tenants/questions or provider failures. Empty-tenant/context failures, the
100-point BM25 cap, blocking stages, non-atomic ingestion and best-effort cache
invalidation remain as documented in [Architecture](architecture.md). Readiness
does not establish provider health or corpus answerability. No new live execution
is part of documentation finalization; regression results should always state
whether they are network-free, isolated Docker integration, or paid live evidence.
