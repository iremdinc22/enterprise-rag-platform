# Why Atlas Deferred Local LLM Integration

Atlas evaluated local inference as a potential extension, then chose not to
integrate it into the current release. The experiments showed that fast local
responses were achievable, but the tested models did not consistently meet the
application's evidence and citation requirements. This summary explains that
decision; the [detailed benchmark report](local-llm-benchmark.md) provides methods,
measurements and links to the archived results.

## Why We Considered Local LLMs

A locally executable RAG experience could reduce dependence on external APIs and
avoid provider inference charges. It could also make Atlas easier to demonstrate
without credentials or paid access. We wanted to explore that possibility while
preserving the qualities that make document question answering useful: answers
supported by retrieved evidence, verifiable citations and predictable operation.
Local execution was therefore a candidate architecture, not a goal that outweighed
answer quality.

## Our Initial Approach

We selected native macOS Ollama to use Apple Silicon's Metal acceleration rather
than running inference inside Docker. Qwen3 1.7B Q4_K_M was the initial compact,
quantized generation candidate; Nomic Embed Text v1.5 supplied semantic embeddings.
These components addressed different problems: selecting relevant evidence and
producing an answer from it.

The target machine had an Apple M2 and 8 GiB of unified memory. Docker was stopped
for the standalone benchmarks. We used synthetic inputs, disabled cloud inference
and thinking, limited context to 2048 tokens and output to 180 tokens, and allowed
one active request and one loaded model. Memory monitoring could terminate the
experiment if resource pressure persisted.

## First Experiment: Qwen3 1.7B

Performance initially looked promising. On the common comparison scenarios,
median generation throughput was approximately 63 tokens per second, and short
warm responses generally arrived in about a second. However, sustained memory
pressure eventually triggered the safety stop, despite no measured swap growth
in that run.

The more consequential issue was reliability. Correct allowance amounts did not
always come with inline citations matching the declared source IDs. Corrective
feedback helped some responses, but not all. In a separate empty-context test,
the model invented an explanation and cited a nonexistent source; its next attempt
was interrupted by the memory stop. Valid structured JSON did not establish that
an answer was grounded.

## Second Experiment: Qwen3 0.6B

We tested the smaller Qwen3 0.6B Q4_K_M to reduce memory demand. It approximately
doubled median throughput to 126 tokens per second and reduced reported loaded
model memory from about 1.53 GiB to 753 MiB. Sampled memory pressure remained
normal, with no swap growth.

For a fair quality comparison, both models used the same five completed scenarios,
production instructions, schema and bounded correction policy. The smaller model
still failed to produce a valid cited answer for any of the four supported
questions within two attempts; 1.7B passed three. The smaller model also invented
explanations in both empty-context attempts. It produced the required abstention
for an unsupported dental question only after correction. Better resource behavior
had not solved the release-blocking reliability problem.

## An Unexpected Embedding Discovery

Nomic produced identical 768-dimensional vectors for exact questions about Orion
and Beacon. This was not rounding: every component matched, including in separate
single-input requests. Removing the query prefix did not resolve it. Lowercasing
only the entity names reduced cosine similarity from 1.0 to approximately 0.794.

These observations suggest a casing/tokenizer preprocessing issue in the tested
combination, but the exact cause was not proven. Literal vocabulary lookups were
not runtime tokenization traces. We did not change production preprocessing based
on that hypothesis.

On an expanded synthetic dataset, lowercase embedding inputs improved vector
Top-1 retrieval from three to eight correct results out of eight. Offline BM25
plus reciprocal rank fusion remained at six: lexical rankings still influenced
which policy appeared first. CrossEncoder reranking and MMR were excluded from
that experiment, so it did not evaluate Atlas's complete retrieval pipeline.

## Why We Decided Against Integration

Atlas needs more than fast text generation. Evidence-grounded answers, citation
integrity and operational reliability take precedence over adding another provider.
We kept validation intact and did not insert citations automatically to conceal
failures. Integrating either candidate would have added lifecycle, networking and
resource-management work without meeting the observed answer-quality criteria.
The decision was NO-GO for this release, rather than accepting weaker guarantees.

## Final Architecture

The application retains its OpenAI-based backend architecture, existing hybrid
retrieval and citation validation. Its free, deterministic localhost demo uses
real ingestion, retrieval and source citations with clearly labeled simulated,
extractive answers. That demo is not a real LLM. Ollama was evaluated experimentally
and was not integrated into the application.

## Lessons Learned

Model selection must consider citation discipline and abstention alongside speed.
Hardware limits require explicit measurement and stop conditions. Retrieval
branches need separate evaluation, followed by evaluation of their combined ranking.
Structured output checks and factual grounding answer different questions.

These conclusions come from a small synthetic dataset, limited model selection,
a fixed seed and hardware-specific runs with differing system baselines. Neither
a complete local-LLM RAG pipeline nor concurrent Docker/Ollama operation was
validated. The findings do not generalize to all local models. For Atlas, preserving
a working demonstration was the evidence-based choice; future integration would
require stronger quality evidence, not merely a faster benchmark.
