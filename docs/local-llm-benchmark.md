# Local LLM Benchmark and Engineering Decision

**Decision: NO-GO for Ollama integration in the current release.** Local inference
was experimentally evaluated, not integrated into Atlas. The smaller model was
faster and more memory-efficient, but failed the citation and abstention criteria.
The existing OpenAI backend and deterministic, API-free local demo remain intact.

## Motivation

Local inference could reduce external API dependencies and provider charges while
making the RAG demonstration more accessible. The evaluation asked whether modest
hardware could deliver both acceptable latency and trustworthy, source-grounded
answers. Speed alone was not an acceptance criterion.

## Experimental environment

- Apple M2 with 8 GiB unified memory; native macOS Ollama 0.24.0 using Metal.
- Docker stopped during standalone inference benchmarks. An earlier resource
  preflight was stopped before model inference because memory pressure persisted.
- 2048-token context, maximum 180 output tokens, thinking disabled.
- One active inference request and at most one loaded model; cloud disabled.
- Temperature 0, seed 42; non-streaming structured JSON responses.
- Synthetic documents only; no application Qdrant/Redis access or paid inference.
- Memory sampled approximately every two seconds. Stop on critical pressure,
  warning pressure sustained for ten seconds, or 512 MiB swap growth from baseline.

Recorded settings describe this experiment, not new application defaults. Model
loads were cold with respect to Ollama residency, not necessarily the operating
system's file cache. Measurements are observations, not performance estimates.

## Model selection

| Model | Role and rationale | Recorded digest |
| --- | --- | --- |
| `qwen3:1.7b-q4_K_M` | Initial small quantized generation candidate | `8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7` |
| `qwen3:0.6b-q4_K_M` | Lower-memory generation alternative | `7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435` |
| `nomic-embed-text:v1.5` | Local semantic embedding candidate; 768-dimensional outputs | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |

Model tags are identifiers, not measured parameter counts. Recorded package
metadata reports approximately 2.0B and 751.63M parameters for the two Qwen tags.
Nomic's recorded package size is 274,302,450 bytes. Digests identify tested
packages; they do not imply that another package version behaves identically.

## Protocol and evidence

[Sanitized artifacts](benchmarks/README.md) preserve the four available result
files and their provenance. The first 1.7B run used shorter exploratory
instructions; it is **excluded** from the generation-quality comparison below.
Both comparison runs used the existing production system instructions, the same
JSON schema and explicit inline-citation reminder, and the same bounded correction
message after failed validation. This was a standalone harness, not `run_rag()`.

The common comparison set has five scenarios: Orion allowance exact wording and
paraphrase, Beacon allowance exact wording and paraphrase, and an unsupported
dental-insurance question. Sources state Orion's allowance as 500 USD and Beacon's
as 2000 USD per year. Questions contain no expected amounts. Both sources are
supplied to generation, independently of retrieval ranking.

Success requires valid fields/types, inline IDs matching declared IDs, only
available source IDs, and citations for factual answers. Abstention must set
`answered=false`, return no IDs and match the application's exact abstention
message. Answer correctness is checked separately against the synthetic evidence;
JSON validity alone is insufficient. The harness's expected-ID check is stricter
than merely mentioning a correct amount.

## Comparative model benchmark

Latency and throughput below use only the common five scenarios. Different retry
counts mean nine completed attempts for 1.7B and ten for 0.6B. Throughput is output
tokens divided by reported evaluation time, including JSON output tokens.

| Measurement | Qwen3 1.7B Q4_K_M | Qwen3 0.6B Q4_K_M |
| --- | ---: | ---: |
| Package disk size, bytes | 1,359,293,444 | 522,653,767 |
| Ollama-reported loaded size, bytes | 1,644,836,896 | 789,521,440 |
| Loaded size, approximately | 1.53 GiB | 753 MiB |
| First model-load-inclusive response | 2.407 s | 1.253 s |
| Subsequent warm responses, range | 0.787–1.283 s | 0.368–0.945 s |
| Median generation throughput | 62.97 tokens/s | 126.275 tokens/s |
| JSON field/type compliance, completed attempts | 9/9 | 10/10 |
| First-attempt policy success, scenarios | 1/5 | 0/5 |
| Policy success within two attempts, scenarios | 4/5 | 1/5 |
| Valid cited answer within two attempts, supported scenarios | 3/4 | 0/4 |
| Unsupported dental question | Exact abstention on attempt 2 | Exact abstention on attempt 2 |

Loaded size is an Ollama residency measurement, not total process or system memory;
the 1.7B residency value comes from the exploratory run of the same package.

### Memory observations

| Run | Start → final swap | Maximum sampled compressed memory | Safety outcome |
| --- | --- | ---: | --- |
| 1.7B exploratory | 696.44 → 648.38 MiB | 2536.3 MiB | Completed; transient warnings |
| 1.7B production-prompt checks | 648.38 → 648.38 MiB | 2434.9 MiB | Sustained warning; server terminated |
| 0.6B evaluation | 592.31 → 592.31 MiB | 2309.4 MiB | Completed; all samples normal |
| Additional Nomic casing check | 592.31 → 592.31 MiB | See artifact samples | Completed; all samples normal |

There was no measured swap growth in these runs. The 1.7B safety stop was triggered
by sustained memory pressure, not by swap growth. Baselines differ, and this is
not a controlled claim that the model alone caused all system memory pressure.
The smaller model demonstrated better resource behavior in this test, but did not
meet answer-validation requirements.

### Per-scenario policy results

| Scenario | 1.7B | 0.6B |
| --- | --- | --- |
| Orion exact | Pass, attempt 1 | Fail after attempt 2 |
| Orion paraphrase | Pass, attempt 2 | Fail after attempt 2 |
| Beacon exact | Fail after attempt 2 | Fail after attempt 2 |
| Beacon paraphrase | Pass, attempt 2 | Fail after attempt 2 |
| Unsupported dental question | Pass, attempt 2 | Pass, attempt 2 |

Both models produced correct allowance amounts, but that did not make their
responses valid cited answers. In particular, 0.6B omitted inline citations even
after corrective feedback; one scenario also returned an empty `citation_ids`.

## Embedding collision investigation

The exact Orion and Beacon questions produced identical Nomic vectors:

- 768 of 768 components equal; maximum absolute difference **0.0**.
- Cosine similarity **1.0**, not a rounded approximation hiding a small difference.
- Both vector norms approximately **1.00000018**.
- Single and batch results matched component-for-component.
- Removing the query prefix did not remove the collision.
- With all query text lowercased, cosine became **0.7977569280**.
- Lowercasing **only entity names** reduced cosine to **0.7939859125**;
  maximum component difference became approximately **0.07969**.

The harness used distinct question strings, mapped outputs in request order and
used `search_query:` / `search_document:` prefixes. The raw payload/vector files
were separate diagnostic artifacts; the four preserved result files contain the
comparison measurements, not a complete transport trace or all vector components.

The recorded metadata identifies a BERT tokenizer. Literal vocabulary lookups for
`Orion`, `Beacon`, `orion` and `beacon` returned no direct entries. That does **not**
prove those words become unknown tokens: tokenizers can use boundary markers and
subword pieces. The saved vocabulary lookup is not a runtime token-ID trace.

The casing experiment strongly suggests a casing/tokenizer preprocessing issue
in this tested model/runtime combination. **The exact root cause was not proven.**
No production preprocessing or identifier normalization was changed, and this
experiment does not justify universally lowercasing application data.

## Retrieval evaluation

Eight synthetic documents included similar allowance policies for Orion, Beacon,
Cedar and Maple, plus leave, travel and internet policies. Eight questions covered
exact wording and paraphrases. This expanded dataset differs from the earlier
four-document exploratory test and is compared only against itself.

| Configuration | Vector Top-1 | Offline BM25 + RRF Top-1 |
| --- | ---: | ---: |
| Original casing | 3/8 | 6/8 |
| Lowercase embedding inputs | 8/8 | 6/8 |

The offline experiment reused the repository's pure tokenization and RRF functions,
BM25Okapi, five candidates per branch and RRF `k=60`. It did not connect to a vector
database. BM25 lowercases its own lexical input in both configurations.

Lowercase embedding inputs repaired vector Top-1 on this small set, but lexical
rankings still affected fusion. Orion's paraphrased allowance question ranked the
internet policy first after fusion; Beacon's paraphrase also ranked a different
policy first. Thus improving one retrieval branch did not guarantee a better
final ranking. **CrossEncoder reranking and MMR were not included**; these results
are not a benchmark of Atlas's complete retrieval pipeline or tenant isolation.

## Structured output vs. semantic reliability

Five properties must be distinguished:

1. **Valid JSON:** the response parses and has the required fields/types.
2. **Correct answer:** the facts match the supplied evidence and requested entity.
3. **Valid citations:** inline references match declared IDs and available sources.
4. **Evidence grounding:** cited material actually supports the claims.
5. **Reliable abstention:** unsupported questions are declined consistently.

The common comparison achieved perfect observed JSON compliance while citation
success remained insufficient. Schema constraints cannot establish factual support.

Empty-context behavior was tested separately and is excluded from the common
comparison table. With 1.7B, the first response invented a general explanation and
cited nonexistent source 1; the second request was interrupted by the memory safety
stop. With 0.6B, both attempts invented explanations and failed validation. These
are observed hallucinations, not merely formatting differences. The unsupported
dental scenario required a second attempt to match the exact abstention contract
with either model.

No citations were automatically inserted, and validation was not weakened to hide
these failures. Existing checks would reject these invalid responses; that protects
integrity but would produce an unreliable user experience if failures were frequent.

## Engineering trade-offs

- Smaller weights improved latency and residency, but citation quality regressed.
- Memory stability matters beyond average throughput on an 8 GiB shared-memory host.
- Embedding discrimination and hybrid ranking require separate evaluation.
- Native execution adds host/container networking, lifecycle and configuration work.
- A local provider must preserve deadlines, tenant scope, cache separation and
  structured citation checks; a fast standalone API is not sufficient.
- Keeping a reproducible working demo is more valuable than adding an unreliable
  provider option to the release.

## Final architectural decision

Defer Ollama integration for this release. The decision is based on failed quality
criteria, with additional resource limitations observed for 1.7B; it is not a claim
that local RAG is inherently infeasible.

Atlas retains its OpenAI-based backend architecture and its explicitly labeled,
deterministic localhost demo. That demo uses real document ingestion/retrieval and
citations with simulated extractive answers. **It is not a real LLM.** No Ollama
provider, model-switching UI or new runtime dependency was added to the application.

## Experimental limitations

- Small synthetic dataset, fixed seed and limited model selection.
- Hardware/runtime-specific timing; no repeated performance distribution study.
- Different system baselines and unequal retry counts; one interrupted 1.7B request.
- Loaded memory is not total system memory; cold-load measurements may benefit
  from operating system file caching.
- No complete local-LLM RAG pipeline evaluation.
- No concurrent Docker/Ollama deployment or container-to-host integration validation.
- No multilingual quality evaluation, adversarial security certification or real
  corpus evaluation.
- No confirmed tokenizer root cause or preserved runtime token-ID trace.
- Results cannot be generalized to all local models, quantizations or hardware.

## Future considerations

A future separately scoped study could assess stronger models on higher-memory
hardware, multilingual embedding alternatives, larger labeled retrieval sets and
more reliable grounded-output strategies. Such work should preserve hard validation
and evaluate the full pipeline. None of these improvements is implemented or initiated
by this documentation task.
