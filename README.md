# Enterprise RAG Platform

A production-oriented **Retrieval-Augmented Generation (RAG)** platform designed to explore the engineering challenges that appear when RAG systems move beyond simple `embed → retrieve → generate` workflows.

The platform combines document ingestion, hybrid retrieval, reranking, diversity-aware context selection, grounded generation, citations, authentication, and tenant-aware retrieval into an explicit and testable pipeline.

> **Status:** Active development — the core RAG pipeline, authentication, and multi-tenant retrieval isolation are implemented. Caching, evaluation, observability, and production API layers are next.

---

## Architecture

```text
                         ┌─────────────────────┐
                         │    Authenticated    │
                         │        User         │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │     UserContext     │
                         │ user_id · tenant_id │
                         │        role         │
                         └──────────┬──────────┘
                                    │
                                    ▼
                              User Query
                                    │
                                    ▼
                 ┌─────────────────────────────────┐
                 │      Tenant-Aware Retrieval     │
                 │                                 │
                 │      BM25          Vector       │
                 │     Lexical       Semantic      │
                 │        └──────┬───────┘         │
                 │               ▼                 │
                 │    Reciprocal Rank Fusion       │
                 └───────────────┬─────────────────┘
                                 │
                                 ▼
                       Exact Deduplication
                                 │
                                 ▼
                       Cross-Encoder Reranking
                                 │
                                 ▼
                        MMR Context Selection
                                 │
                                 ▼
                       Citations & Provenance
                                 │
                                 ▼
                        Grounded Generation
                                 │
                                 ▼
                      Answer + Source Citations
```

Documents enter the system through a separate ingestion path:

```text
PDF
 ↓
Parse
 ↓
Chunk + Metadata
 ↓
Batch Embedding
 ↓
Qdrant
```

Ingestion can also run asynchronously:

```text
Request → Redis → Celery Worker → Ingestion Pipeline
```

This keeps expensive document processing outside the synchronous request path.

---

## Why This Project?

A basic RAG prototype can retrieve semantically similar chunks and send them directly to an LLM.

As the system grows, additional engineering problems appear:

- semantic search may miss exact identifiers and domain-specific terminology,
- lexical and vector scores are not directly comparable,
- retrieved candidates may contain redundant information,
- duplicate content can exist across multiple sources,
- generated claims need traceable evidence,
- ingestion should not block application requests,
- users must not retrieve documents belonging to another tenant,
- retrieval does not necessarily mean the available evidence can answer the question.

This project handles these concerns as explicit layers that can be inspected, tested, and improved independently.

---

## Hybrid Retrieval

The retrieval pipeline combines two complementary strategies:

- **BM25** for lexical matches, identifiers, technical terms, and exact phrases.
- **Vector search** for semantic similarity.

Their raw scores are not directly combined because they represent different scoring systems.

Instead, rankings are fused using **Reciprocal Rank Fusion (RRF)**:

```text
Query
 ├── BM25
 │
 └── Vector Search
        │
        ▼
       RRF
        │
        ▼
 Candidate Pool
```

RRF combines rank positions rather than assuming BM25 and vector similarity scores are directly comparable.

---

## Reranking & Context Selection

Hybrid retrieval produces candidate documents, but first-stage retrieval ranking is not necessarily precise enough for the final LLM context.

Candidates are reranked using:

`cross-encoder/ms-marco-MiniLM-L6-v2`

The Cross-Encoder evaluates the **query and candidate together**, providing a stronger relevance signal over the smaller candidate pool.

The pipeline then applies:

```text
Hybrid Retrieval
       ↓
Exact Deduplication
       ↓
Cross-Encoder Reranking
       ↓
MMR Context Selection
       ↓
Final Context
```

**Maximal Marginal Relevance (MMR)** balances relevance against redundancy so the final context is not unnecessarily filled with chunks containing nearly identical information.

---

## Citations & Provenance

Every indexed chunk carries provenance metadata:

- document identifier,
- filename,
- page,
- chunk identifier.

This metadata survives retrieval, fusion, reranking, deduplication, and context selection.

Selected evidence can be represented to the LLM as:

```text
[SOURCE 1]
Provenance:
- File: employee-handbook.pdf | Page: 2 | Chunk: chunk-002

Content:
Remote employees must connect to internal company systems
through the approved VPN service.
```

The generated answer can reference the evidence using `[1]`, while the application retains structured citation metadata for API or frontend use.

### Provenance-Aware Deduplication

Identical content may appear in multiple documents.

Instead of discarding the additional provenance when duplicate text is removed, the platform represents the content once while preserving all known sources:

```text
                 ┌─ employee-handbook.pdf
Unique Content ──┤
                 └─ employee-handbook-v2.pdf
```

This reduces redundant context without losing traceability.

---

## Grounded Generation

The final selected context is converted into citation-aware input before being sent to the LLM.

```text
Selected Context
       ↓
Citation Builder
       ↓
Grounded Generation
       ↓
Answer + Citations
```

The generation layer is instructed to:

- answer using only the provided evidence,
- cite the sources supporting its claims,
- avoid unsupported claims,
- return structured output,
- indicate when the available evidence is insufficient.

Example:

```text
Question:
What is the remote work allowance?

Answer:
Employees working remotely receive an annual home office
allowance of 500 USD. [1]
```

If the retrieved evidence cannot support the requested answer, the system returns a no-answer response with no citations.

This separates **retrieval** from **answerability**: retrieving candidates does not automatically mean the question can be answered from them.

---

## Authentication & Authorization

Authentication uses signed **JWT access tokens**.

Validated token claims are converted into a typed `UserContext`:

```text
JWT
 ↓
Signature + Claim Validation
 ↓
UserContext
 ├── user_id
 ├── tenant_id
 └── role
```

Authentication and authorization remain separate concerns.

The authenticated identity determines the trusted user context, while authorization rules determine which operations that user may perform.

---

## Multi-Tenant RAG

Tenant isolation is enforced inside the retrieval pipeline.

Both retrieval branches receive the tenant identifier from the authenticated `UserContext`:

```text
                 UserContext
                     │
                  tenant_id
                     │
            ┌────────┴────────┐
            ▼                 ▼
          BM25             Vector
     Tenant Filter      Tenant Filter
            │                 │
            └────────┬────────┘
                     ▼
                    RRF
                     ↓
                 Reranking
                     ↓
                    MMR
                     ↓
                Generation
```

Filtering occurs **before or during retrieval**, rather than retrieving globally and removing unauthorized documents afterward.

This means another tenant's documents never enter RRF, reranking, context selection, or generation.

### Isolation Example

The test dataset contains the same logical policy under two tenants:

```text
ACME
remote-work-policy → 500 USD annual allowance

GLOBEX
remote-work-policy → 2000 USD annual allowance
```

For the same question:

```text
"What is the remote work allowance?"
```

an ACME user receives an answer grounded in `acme-policy.pdf`, while a GLOBEX user receives an answer grounded in `globex-policy.pdf`.

Cross-tenant access is also tested explicitly:

```text
Authenticated tenant: ACME

Question:
"What is GLOBEX's remote work allowance?"
```

GLOBEX documents do not enter the retrieved context, so the generation layer returns a no-answer response instead of exposing the GLOBEX policy.

The query determines **what the user wants to find**; the authenticated context determines **which data the user is allowed to search**.

---

## Document Ingestion & Background Processing

The ingestion pipeline currently supports:

- page-aware PDF parsing,
- configurable chunking and overlap,
- batch embedding generation,
- metadata-preserving storage,
- tenant-aware vector storage,
- stable UUID-based identifiers,
- idempotent document re-ingestion,
- stale chunk removal.

Long-running ingestion can be moved to **Celery workers** using Redis as the broker.

Job states are tracked independently:

```text
queued → processing → completed
```

with failure and retry handling for unsuccessful jobs.

---

## Validation & Experiments

Individual stages are tested through experiment scripts rather than evaluating the system only from the final LLM answer.

Current multi-tenant validation includes:

```text
Tenant-Aware Ingestion          ✓
Vector Retrieval Isolation      ✓
BM25 Retrieval Isolation        ✓
Hybrid + RRF Isolation          ✓
Context Selection Isolation     ✓
End-to-End RAG Isolation        ✓
Cross-Tenant Leakage Test       ✓
```

Experiments are also used to inspect BM25 behavior, lexical vs semantic retrieval, RRF contributions, reranker scores, MMR selection, and citation provenance.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python |
| LLM | OpenAI |
| Embeddings | OpenAI `text-embedding-3-small` |
| Vector Database | Qdrant |
| Lexical Retrieval | BM25 / `rank-bm25` |
| Rank Fusion | Reciprocal Rank Fusion |
| Reranking | Sentence Transformers CrossEncoder |
| Context Selection | Maximal Marginal Relevance |
| Authentication | JWT / PyJWT |
| Validation | Pydantic |
| Background Processing | Celery |
| Broker / Job Store | Redis |
| PDF Processing | pypdf |
| Infrastructure | Docker |

---

## Current Capabilities

**Ingestion**  
PDF parsing · chunking · batch embeddings · stable IDs · tenant-aware storage · idempotent re-ingestion

**Retrieval**  
BM25 · semantic search · hybrid retrieval · RRF

**Retrieval Quality**  
Cross-Encoder reranking · exact deduplication · provenance-aware deduplication · MMR

**Generation & Traceability**  
Grounded generation · structured output · no-answer behavior · citations · source provenance

**Security**  
JWT authentication · typed user context · role-based authorization · tenant-aware retrieval · cross-tenant isolation

**Processing**  
Celery background workers · Redis job tracking · retry handling

---

## Roadmap

```text
Document Ingestion          ████████████████████  Implemented
Async Processing            ████████████████████  Implemented
Hybrid Retrieval            ████████████████████  Implemented
Reranking & Selection       ████████████████████  Implemented
Citations & Provenance      ████████████████████  Implemented
Grounded Generation         ████████████████████  Implemented
Authentication              ████████████████████  Implemented
Multi-Tenant RAG            ████████████████████  Implemented

Caching                     ░░░░░░░░░░░░░░░░░░░░  Next
Resilience                  ░░░░░░░░░░░░░░░░░░░░
Evaluation                  ░░░░░░░░░░░░░░░░░░░░
Observability               ░░░░░░░░░░░░░░░░░░░░
Latency & Cost Tracking     ░░░░░░░░░░░░░░░░░░░░
Production API              ░░░░░░░░░░░░░░░░░░░░
Dockerization               ░░░░░░░░░░░░░░░░░░░░
E2E & Adversarial Testing   ░░░░░░░░░░░░░░░░░░░░
```

---

## Design Philosophy

The project deliberately keeps the major RAG stages explicit:

```text
authenticate
     ↓
retrieve
     ↓
fuse
     ↓
deduplicate
     ↓
rerank
     ↓
diversify
     ↓
trace
     ↓
generate
```

Rather than treating RAG as a single black-box operation, each stage can be inspected and evaluated independently.

The long-term goal is not simply to make an LLM answer questions over documents, but to build a RAG system whose **retrieval quality, evidence, permissions, failures, latency, and cost can be measured and systematically improved**.