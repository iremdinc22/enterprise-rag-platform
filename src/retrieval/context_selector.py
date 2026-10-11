
from src.ingestion.embedder import create_embeddings
from src.observability.tracing import get_tracer
from src.retrieval.deduplicator import deduplicate_exact
from src.retrieval.hybrid_retriever import hybrid_search
from src.retrieval.mmr import select_with_mmr
from src.retrieval.reranker import rerank


async def select_context(
    query,
    tenant_id,
    retrieval_limit=5,
    final_limit=3,
    lambda_value=0.5
):
    if not query or not query.strip():
        raise ValueError("query cannot be empty")

    if not tenant_id or not tenant_id.strip():
        raise ValueError("tenant_id cannot be empty")

    if retrieval_limit <= 0:
        raise ValueError(
            "retrieval_limit must be greater than 0"
        )

    if final_limit <= 0:
        raise ValueError(
            "final_limit must be greater than 0"
        )

    if final_limit > retrieval_limit:
        raise ValueError(
            "final_limit cannot be greater than retrieval_limit"
        )

    tracer = get_tracer()

    # Retrieve tenant-scoped candidates
    with tracer.start_as_current_span(
        "retrieval.hybrid_search"
    ) as span:
        hybrid_results = await hybrid_search(
            query=query,
            tenant_id=tenant_id,
            retrieval_limit=retrieval_limit,
            final_limit=retrieval_limit
        )

        span.set_attribute(
            "retrieval.hybrid_candidate_count",
            len(hybrid_results)
        )

    # Remove exact duplicate chunks
    with tracer.start_as_current_span(
        "retrieval.deduplication"
    ) as span:
        unique_candidates = deduplicate_exact(
            hybrid_results
        )

        span.set_attribute(
            "retrieval.unique_candidate_count",
            len(unique_candidates)
        )
        span.set_attribute(
            "retrieval.duplicates_removed",
            len(hybrid_results) - len(unique_candidates)
        )

    if not unique_candidates:
        return []

    # Refine candidate relevance
    with tracer.start_as_current_span(
        "retrieval.reranking"
    ) as span:
        reranked_candidates = rerank(
            query=query,
            candidates=unique_candidates,
            limit=len(unique_candidates)
        )

        span.set_attribute(
            "retrieval.reranked_candidate_count",
            len(reranked_candidates)
        )

    # Create embeddings for MMR
    texts = [
        candidate["text"]
        for candidate in reranked_candidates
    ]

    with tracer.start_as_current_span(
        "retrieval.candidate_embeddings"
    ) as span:
        embeddings = await create_embeddings(texts)

        span.set_attribute(
            "retrieval.embedding_count",
            len(embeddings)
        )

    for candidate, embedding in zip(
        reranked_candidates,
        embeddings
    ):
        candidate["embedding"] = embedding

    # Select final context with MMR
    with tracer.start_as_current_span(
        "retrieval.mmr"
    ) as span:
        selected_context = select_with_mmr(
            candidates=reranked_candidates,
            limit=final_limit,
            lambda_value=lambda_value
        )

        span.set_attribute(
            "retrieval.selected_context_count",
            len(selected_context)
        )
        span.set_attribute(
            "retrieval.mmr_lambda",
            lambda_value
        )

    return selected_context
