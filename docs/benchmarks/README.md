# Local inference benchmark evidence

These sanitized JSON files support the [technical report](../local-llm-benchmark.md).
They contain synthetic responses, timings, model metadata and relative-time memory
samples. They are experimental evidence, not application fixtures or performance
promises.

| File | Purpose |
| --- | --- |
| [qwen17-exploratory.json](qwen17-exploratory.json) | Initial short-prompt run, embedding timings, model residency and digests |
| [qwen17-production-prompt.json](qwen17-production-prompt.json) | Production-instruction comparison; final request interrupted by memory safety stop |
| [qwen06-evaluation.json](qwen06-evaluation.json) | Lightweight model comparison, raw-vector equality metrics and expanded retrieval test |
| [nomic-casing.json](nomic-casing.json) | Entity-only casing comparison and lowercase retrieval evaluation |
| [manifest.json](manifest.json) | Source basenames/groups, source SHA256, sanitized artifact SHA256 and transformation |

All four requested source artifacts were available. Residency expiry timestamps
were removed recursively; recorded measurements and synthetic model responses were
preserved. No credentials, machine identifiers or personal filesystem paths are
included. Source hashes refer to the original inputs and therefore differ from
sanitized artifact hashes when timestamps were removed.

A `failure` row in the 1.7B checks records the intentionally interrupted request,
not a completed model response. A `final_models` row is an immediate residency
snapshot: the Nomic casing run still reported residency just after the unload
request. It does not prove unloading had completed. Process termination was verified
separately during the experiment; no post-termination process receipt is present
in these four files.

Vocabulary lookups are literal metadata checks, not tokenization traces. Missing
literal entries must not be interpreted as proof of unknown-token substitution.
Raw request/vector diagnostics, full server logs, benchmark executables and a
machine configuration snapshot are not bundled here. The archive is not a complete
reproduction package; missing evidence has not been reconstructed.
