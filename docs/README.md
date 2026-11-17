# Atlas documentation

[Project overview](../README.md) · [Quick start](demo.md#first-startup)

## Run and use Atlas

- [Demo setup](demo.md) — safe credentials, isolated services, provider modes and walkthrough.
- [Dashboard](demo-dashboard.md) — interaction contracts, development and screenshot gallery.
- [Uploads and documents](demo-uploads.md) — PDF validation, jobs, ownership and downloads.
- [API reference](api.md) — query schemas, authentication, HTTP errors and demo routes.
- [Deployment](deployment.md) — original OpenAI backend, service configuration and storage.

## Understand the engineering

- [System architecture](architecture.md#demo-system-topology) — components and full system diagram.
- [RAG and retrieval](architecture.md#question-answering) — cache, fusion, reranking, MMR and citations.
- [Document ingestion](architecture.md#document-indexing) — full pipeline, replacement checks and failure windows.
- [Security and multi-tenancy](architecture.md#security-and-resource-controls) — identity, isolation and resource limits.
- [Engineering decisions](architecture.md#engineering-decisions-and-trade-offs) — rationale and trade-offs.
- [Known limitations](architecture.md#known-limitations) — operational boundaries and production-hardening needs.
- [Deterministic provider](demo-deterministic.md) — local adapters, lexical matching and historical evidence.
- [Local LLM decision](local-llm-decision-summary.md) — why Ollama integration was deferred.
- [Local LLM benchmarks](local-llm-benchmark.md) — measured results and sanitized artifact links.

## Review the evidence

- [Testing](testing.md) — regressions, controlled Docker E2E, live smoke guard and manual CI execution.
- [Final validation](final-validation.md) — 377 consolidated test results, additional controls and release scope.
- [Screenshot provenance](images/README.md) — verified, unmodified application captures.

## Repository map

```text
frontend/              React dashboard, typed client, component and browser tests
src/api/               Normal query API, authentication dependencies, resource limits
src/auth/              JWT helpers and roles
src/demo/              Isolated sessions, uploads, documents and local adapters
src/ingestion/         PDF parsing, chunking and embedding preparation
src/retrieval/         BM25/vector fusion, deduplication, reranking and MMR
src/citations/         Trusted source groups, context and response construction
src/vector_store/      Qdrant operations and ownership checks
src/cache/             Redis embedding/result caches
src/workers/           Celery ingestion
src/observability/     Tracing, query usage and cost estimation
tests/                 Regressions, controlled E2E, HTTP demo and CI helpers
evaluation/            Retrieval/generation metrics and historical results
docs/                  Architecture, contracts, deployment and experimental evidence
.github/workflows/     Manual Docker/browser validation
```
