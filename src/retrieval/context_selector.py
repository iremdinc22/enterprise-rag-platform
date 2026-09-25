from src.ingestion.embedder import create_embeddings
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

    # Retrieve tenant-scoped candidates using lexical + semantic search
    hybrid_results = await hybrid_search(
        query=query,
        tenant_id=tenant_id,
        retrieval_limit=retrieval_limit,
        final_limit=retrieval_limit
    )

    # Remove exact duplicate chunks
    unique_candidates = deduplicate_exact(
        hybrid_results
    )

    if not unique_candidates:
        return []

    # Refine candidate relevance
    reranked_candidates = rerank(
        query=query,
        candidates=unique_candidates,
        limit=len(unique_candidates)
    )

    # Create embeddings for candidate-to-candidate similarity
    texts = [
        candidate["text"]
        for candidate in reranked_candidates
    ]

    embeddings = await create_embeddings(texts)

    for candidate, embedding in zip(
        reranked_candidates,
        embeddings
    ):
        candidate["embedding"] = embedding

    # Select relevant but non-redundant final context
    selected_context = select_with_mmr(
        candidates=reranked_candidates,
        limit=final_limit,
        lambda_value=lambda_value
    )

    return selected_context