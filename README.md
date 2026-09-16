# Enterprise RAG Platform

A production-oriented **Retrieval-Augmented Generation (RAG)** platform built to explore the engineering challenges behind enterprise RAG systems beyond basic vector search.

Instead of treating RAG as a simple `embed → retrieve → generate` workflow, this project focuses on the surrounding infrastructure required for reliable retrieval: asynchronous ingestion, hybrid search, rank fusion, reranking, context diversity, provenance, and citations.

> **Status:** Active development — retrieval, reranking, context selection, and citation/provenance layers are implemented. The generation pipeline is currently being developed.

---

## Architecture

```text
                         ┌──────────────────┐
                         │    Documents     │
                         └────────┬─────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │   Ingestion Pipeline    │
                    │                         │
                    │ Parse → Chunk → Embed   │
                    └────────────┬────────────┘
                                 │
                                 ▼
                         ┌──────────────┐
                         │   Qdrant     │
                         │ Vector Store │
                         └──────┬───────┘
                                │
                                ▼
              ┌─────────────────────────────────┐
              │        Hybrid Retrieval         │
              │                                 │
              │   BM25               Vector     │
              │  Lexical             Semantic   │
              │      └────────┬────────┘         │
              │               ▼                  │
              │     Reciprocal Rank Fusion       │
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
                       LLM-Ready Context
```

Document ingestion can run asynchronously through **Celery + Redis**, keeping expensive parsing and embedding work outside the request path.

---

## What Makes It Different?

A minimal RAG implementation can retrieve semantically similar chunks and send them directly to an LLM.

That works for a prototype, but introduces several problems as the system grows:

- lexical identifiers may be missed by semantic search,
- retrieved chunks may contain redundant information,
- vector and lexical scores are not directly comparable,
- the most relevant retrieved chunks are not always the best final context,
- duplicate content may destroy source provenance during deduplication,
- generated answers need traceable evidence,
- document ingestion should not block application requests.

This project addresses these problems as separate engineering layers rather than hiding the entire retrieval process behind a single framework abstraction.

---

## Retrieval Pipeline

### Hybrid Search

Queries are retrieved through two complementary strategies:

**BM25** captures lexical matches such as identifiers, technical terms, and exact phrases, while **vector search** captures semantic similarity.

Their raw scores are intentionally not mixed directly. Instead, their rankings are combined using **Reciprocal Rank Fusion (RRF)**.

```text
Query
 ├── BM25 Retrieval
 │
 └── Vector Retrieval
          │
          ▼
 Reciprocal Rank Fusion
          │
          ▼
   Candidate Pool
```

This allows lexical and semantic retrieval to contribute without assuming their scoring systems are comparable.

### Cross-Encoder Reranking

The fused candidate pool is passed through:

`cross-encoder/ms-marco-MiniLM-L6-v2`

Unlike embedding similarity, the Cross-Encoder evaluates the **query and candidate together**, producing a stronger relevance signal for the smaller candidate set.

### Diversity-Aware Context Selection

High relevance alone can produce a context window containing several chunks that repeat essentially the same information.

The pipeline therefore applies:

```text
Exact Deduplication
        ↓
Cross-Encoder Relevance
        ↓
Candidate Similarity
        ↓
Maximal Marginal Relevance
        ↓
Final Context
```

**Maximal Marginal Relevance (MMR)** balances query relevance against similarity to already selected chunks.

The relevance/diversity trade-off is configurable through λ rather than being treated as a fixed universal value.

---

## Citations & Provenance

Retrieval quality is only part of the problem. The system must also preserve **where retrieved information came from**.

Each indexed chunk carries provenance metadata including:

- document identifier,
- filename,
- page,
- chunk identifier.

This metadata survives retrieval, fusion, reranking, and final context selection.

The citation layer transforms selected context into an LLM-readable representation:

```text
[SOURCE 1]
Provenance:
- File: employee-handbook.pdf | Page: 2 | Chunk: chunk-002
- File: employee-handbook-v2.pdf | Page: 2 | Chunk: chunk-002

Content:
Remote employees must connect to internal company systems
through the approved VPN service.
```

### Provenance-Aware Deduplication

Exact duplicate content may exist in multiple documents.

Simply removing the duplicate would also remove evidence that another source contained the same information.

Instead, duplicate content is represented once while its original sources are preserved:

```text
                 ┌─ employee-handbook.pdf
Unique Content ──┤
                 └─ employee-handbook-v2.pdf
```

This allows a single citation to remain connected to multiple underlying sources.

Citation metadata is also available as structured data so an API or frontend can resolve references such as `[1]` back to their original documents.

---

## Document Ingestion

Documents pass through a dedicated ingestion pipeline:

```text
PDF
 ↓
Page Extraction
 ↓
Chunking + Overlap
 ↓
Metadata Attachment
 ↓
Batch Embedding
 ↓
Qdrant
```

The ingestion layer currently supports:

- page-aware PDF parsing,
- configurable chunk size and overlap,
- batch embedding generation,
- stable UUID-based vector identifiers,
- metadata-preserving storage,
- idempotent document re-ingestion,
- stale chunk removal when documents are replaced.

Stable identifiers prevent repeated ingestion from continuously creating duplicate logical chunks.

---

## Asynchronous Processing

Document ingestion can be executed as a background job using **Celery + Redis**.

```text
Ingestion Request
       ↓
   Redis Broker
       ↓
 Celery Worker
       ↓
Parse → Chunk → Embed → Index
       ↓
   Job Status
```

Job state is tracked independently with states such as:

`queued → processing → completed`

with failure and retry states available for unsuccessful jobs.

This separates expensive ingestion work from synchronous application requests and provides the foundation for larger document-processing workloads.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python |
| Embeddings | OpenAI `text-embedding-3-small` |
| Vector Database | Qdrant |
| Lexical Retrieval | BM25 / `rank-bm25` |
| Rank Fusion | Reciprocal Rank Fusion |
| Reranking | Sentence Transformers CrossEncoder |
| Context Selection | Maximal Marginal Relevance |
| Background Processing | Celery |
| Broker / Job Store | Redis |
| PDF Processing | pypdf |
| Infrastructure | Docker |

---

## Current Capabilities

**Ingestion**  
PDF parsing · chunking · embeddings · stable IDs · idempotent re-ingestion

**Processing**  
Background ingestion · Redis job tracking · retry handling

**Retrieval**  
BM25 · semantic search · hybrid retrieval · RRF

**Retrieval Quality**  
Cross-Encoder reranking · exact deduplication · MMR · diversity-aware context selection

**Trust & Traceability**  
Provenance tracking · multi-source deduplication · citation mapping · LLM-ready source context

---

## Roadmap

The retrieval foundation is implemented. Upcoming work expands the system into a complete RAG serving and evaluation platform.

```text
Retrieval Foundation        ████████████████████  Implemented
Citations & Provenance      ████████████████████  Implemented
Generation Pipeline         ░░░░░░░░░░░░░░░░░░░░  Next
Evaluation                  ░░░░░░░░░░░░░░░░░░░░
Permissions                 ░░░░░░░░░░░░░░░░░░░░
Observability               ░░░░░░░░░░░░░░░░░░░░
Production API              ░░░░░░░░░░░░░░░░░░░░
```

Planned capabilities include grounded generation, citation-aware answers, evaluation datasets, permission-aware retrieval, authentication, caching, latency and cost metrics, tracing, and production-oriented API serving.

---

## Design Philosophy

The project deliberately keeps retrieval stages explicit.

Rather than relying on a single high-level RAG abstraction, individual components can be inspected and experimented with independently:

```text
retrieve
   ↓
fuse
   ↓
rerank
   ↓
deduplicate
   ↓
diversify
   ↓
trace
   ↓
generate
```

This makes it possible to reason about **why a chunk was retrieved, why it survived selection, and where its information originated**.

The long-term goal is not simply to make an LLM answer questions over documents, but to build a RAG system whose retrieval quality, evidence, latency, cost, permissions, and failures can be measured and improved.