# Demo PDF upload and ingestion tracking

[Demo setup](demo.md) · [Normal API](api.md) · [Testing](testing.md)

These routes exist only in the activated demo application. The normal FastAPI
entrypoint is unchanged. Document listing, source PDF retrieval and the React UI
are not implemented here.

## Provider safety

The only supported runtime mode remains `disabled`. An authenticated admin upload
with a valid idempotency key receives sanitized 503 **before body reception,
PDF parsing, Redis access, file creation or job acceptance**. No provider-enabled
environment switch or fake production provider is added. Successful admission and
worker execution are exercised only with explicit fake dependencies in tests.
A separately approved simulated provider must be implemented before interactive
uploads can succeed. Never enable a real provider as a troubleshooting step.

## POST /api/v1/documents

Requires JWT Bearer and the existing admin upload permission. Tenant is taken
exclusively from verified claims and must be `acme` or `globex`. No tenant/document
ID, file path, model or task ID is accepted from clients.

Send exactly one `multipart/form-data` part named `file`, with a PDF filename and
`application/pdf` part type. Extra parts/fields, duplicate files, incomplete
multipart messages and unsafe filenames are rejected. `Idempotency-Key` is a
required UUID header (canonical hyphenated representation; case-insensitive UUID
hex). Unknown URL parameters are rejected.

When an approved provider is available, the success contract is HTTP 202:

```json
{
  "document_id": "doc-<server-generated-uuid>",
  "job_id": "<server-generated-uuid>",
  "status": "queued",
  "status_url": "/api/v1/ingestion-jobs/<server-generated-uuid>"
}
```

Replay may show a later status, including `submission_unknown`, `processing`,
`retrying`, `completed` or `failed`; it returns the original IDs and never
republishes. Same tenant/key/identical file bytes reuse the submission; a different
file digest returns 409. Changing the filename does not rename an existing upload.
Different tenants have separate idempotency namespaces.

| Limit | Policy |
| --- | --- |
| PDF file | 5 MiB; existing demo config can lower this |
| Entire multipart body | File allowance + 64 KiB (default 5 MiB + 64 KiB) |
| Body reception | 15 seconds total, not reset per chunk |
| PDF pages | 10; configured limit may be lower |
| PDF chunks | 200; configured limit may be lower |
| Extracted text | 100,000 characters |
| Upload preparation | One per API process; immediate 503, no waiting queue |
| Active/pending jobs | Two globally in demo Redis |
| Accepted uploads | Ten per tenant, twenty globally; persistent Redis counters |
| Job-status reads | Four active per process; immediate 503 at capacity |

Authentication, authorization and disabled-provider rejection precede parsing.
Content-Length is an early check, not trusted accounting: missing, underdeclared
and chunked bodies are byte-counted. Oversize is 413; receive timeout is 408;
malformed framing/disconnect is 400; invalid multipart/PDF is 422 (unsupported
media type 415); authorization is 401/403; invalid key is 422; quota is 429;
capacity or infrastructure failure is sanitized 503.

Global capacity/quota prechecks run before PDF extraction. A final Redis Lua
admission script resolves concurrent requests atomically. Counters increment on
metadata acceptance, after a valid file has been stored. Later publish failures,
worker errors, cancellation and successful completion do not refund upload counts.
Only terminal job states release active-job capacity. Idempotent replays consume
no additional quota. Reads do not consume upload/query quota.

## PDF validation and storage

PDF parsing uses strict `pypdf`, rejects encrypted/textless/corrupt input, checks
page count before extraction, and uses the existing 40-word/8-word-overlap chunker.
Validation runs in a child process: 10-second wall timeout, 5-second CPU limit;
Linux additionally has a 512 MiB address-space limit. On macOS that memory limit is
not supported: CPU/wall protections remain, but there is no hard memory cutoff.
Use the Linux Docker deployment for untrusted PDFs. Unsupported platforms fail
closed. These bounds do not turn the PDF parser into a complete security sandbox.
Child stderr is discarded, server credentials are not inherited in its environment,
and only sanitized validation summaries cross back.

Files are stored as `/demo/uploads/<verified-tenant>/<generated-document-id>/<filename>`.
No paths are returned in API responses. Accepted filenames retain their exact value;
no stripping, lowercasing or Unicode normalization is applied. Path separators,
colon/control characters, non-PDF suffixes and names over 180 UTF-8 bytes are
rejected. Backslashes are rejected before the multipart library can normalize
Windows-style paths.

Storage uses directory descriptors with `O_NOFOLLOW`, exclusive new directories
and files, mode 0700 directories and mode 0400 files. Existing document directories
are never adopted or overwritten. The worker's shared upload mount remains
read-only. The worker verifies file size/hash and validates again, then passes a
private temporary copy with the original filename to the existing ingestion
pipeline. This prevents shared upload path swaps during its second parse.
Immutability is an application/file-permission policy, not protection from a
compromised host or privileged operator.

## GET /api/v1/ingestion-jobs/{job_id}

Requires a valid tenant JWT; admin role is not required for reading an owned job.
Foreign, missing, ambiguous-owner and invalid-format IDs return the same 404
`{"detail":"Job not found"}`. Redis unavailability returns safe 503, not false 404.

```json
{
  "job_id": "<uuid>",
  "document_id": "doc-<uuid>",
  "status": "processing",
  "error": null
}
```

A failed job exposes only:

```json
{"code":"ingestion_failed","message":"Document could not be processed."}
```

Raw worker/provider exceptions, filenames, hashes, filesystem paths and document
text are not returned. State transitions are:

`submission_unknown → queued → processing → completed/failed`

A retryable worker error permits `processing → retrying → processing` with at most
three Celery retries (four attempts), reusing the same job ID. Claiming an attempt
is an atomic metadata transition, so a duplicate delivery does not execute it twice.
A worker can claim/finish before publish acknowledgement; acknowledgement never
downgrades that state. The demo worker uses only queue `demo.ingestion`, JSON
messages and one task per child to isolate existing async client lifecycles.
The normal worker and ingestion pipeline are unchanged.

## Publishing uncertainty and remaining limits

Redis metadata and Celery publishing are **not one transaction**. Metadata begins
in `submission_unknown`; publication acknowledgement moves it to `queued` only if
work has not already progressed. Celery publication retries are disabled (`retry=False`) for
this demo publisher; fixed task IDs and atomic attempt claims handle duplicate deliveries. Only a known pre-transport failure is marked failed; any
ambiguous broker error retains the reservation and returns 503 with
`code: "submission_unknown"`, job ID and status URL. Do not automatically resubmit
or create a replacement idempotency key. Poll the original owned job instead.

A lost Redis reservation reply can also hide an applied write. The API retains
its candidate file, never publishes on an unverified reservation, and reports an
unknown outcome. Replay resolves the same record if present; it never guesses that
it should publish. A crash/unknown state may hold capacity indefinitely. Operator
reconciliation, orphan-file cleanup, cancellation and automatic recovery are not
implemented; no existing data is deleted to resolve uncertainty.

Before invoking existing ingestion, the worker makes a bounded exact-tenant/document
Qdrant existence check and refuses already indexed documents, including partial
writes from a prior ambiguous attempt. This is not an atomic Qdrant create or a
lock against independent writers. The reused pipeline still has delete-then-upsert
and best-effort cache invalidation behavior: failures can leave partial new points,
and failed jobs may therefore have partial searchable data. No rollback, generation
switching, atomic replacement, hard spending cap or public-deployment guarantee is
claimed. Default disabled mode prevents provider-dependent jobs from being admitted.

## Network-free verification

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
python -m unittest tests.test_demo_uploads -v
python -m unittest discover -s tests -v
python -m unittest discover -s evaluation/tests -p 'test_gener*.py' -v
python -m compileall -q src tests
git diff --check
```

`python-multipart` is the only new runtime dependency. `fakeredis[lua]` is test-only:
these tests execute the actual Lua scripts without any Redis socket or server.
Synthetic PDFs, temporary files, fake publishers and fake pipelines are used;
existing documents/collections, containers and environment files are untouched.
Runtime upload verification and Docker integration are separate approval steps.

## Tenant-owned document access

The isolated demo API also provides authenticated, read-only source access:

- `GET /api/v1/documents?limit=20&offset=0`: `limit` is 1–50 and
  `offset` is nonnegative. Returns `documents`, `total`, `limit`, and `offset`.
  Each document contains `document_id`, `job_id`, `filename`, `size_bytes`,
  `page_count`, `created_at`, and current `status`. Results include all accepted
  submission statuses, sorted newest first (document ID breaks timestamp ties).
- `GET /api/v1/documents/{document_id}/file`: returns the original PDF to its
  authenticated tenant, including while ingestion is pending or failed.
  Foreign, missing, malformed IDs, missing files, unsafe filesystem entries,
  and file digest/size mismatches return the same generic 404 response.

Both admin and employee roles may read their own tenant's documents. Tenant
selection comes only from verified JWT claims. These routes are absent from the
normal API and do not enable providers. Supply Bearer authentication headers;
never place tokens in URLs. Citation document IDs and page numbers are unchanged;
a future dashboard can fetch the authorized bytes and open a local viewer at the
cited page.

PDF responses use `application/pdf`, `Content-Disposition: attachment` with a
safely encoded original filename, `Cache-Control: no-store`, `nosniff`, and a
sandbox CSP. Files are read through no-follow directory/file descriptors after
ownership validation, bounded to the upload limit and checked against the stored
SHA-256 and size. No private path is returned. This is source download, not PDF
content sanitization; downloaded PDFs remain untrusted documents. HTTP Range and
inline preview are not implemented.

Listing uses existing document metadata without an index migration. Work is
bounded to 64 SCAN calls and 256 distinct document keys; unexpected database
expansion or malformed owned metadata fails safely with 503. SCAN pagination is
not a transactional snapshot: concurrent status updates/submissions can change
results between requests. Four shared status/document read slots bound blocking
work, retain slots through cancellation cleanup, and reject overload with 503.
PDF bytes are buffered (at most 5 MiB per read); this is not a streaming download.

Network-free verification: `python -m unittest tests.test_demo_documents -v`.
Tests use only temporary files and fake Redis; no provider or existing corpus
access is required.
