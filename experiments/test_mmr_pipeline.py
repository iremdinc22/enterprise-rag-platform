import asyncio

from src.ingestion.embedder import create_embeddings
from src.retrieval.deduplicator import deduplicate_exact
from src.retrieval.hybrid_retriever import hybrid_search
from src.retrieval.mmr import select_with_mmr
from src.retrieval.reranker import rerank


async def main():
    query = "remote employee network access"

    # 1. Retrieve a larger candidate pool
    hybrid_results = await hybrid_search(
        query=query,
        retrieval_limit=5,
        final_limit=5
    )

    # 2. Remove exact duplicate chunks
    unique_candidates = deduplicate_exact(
        hybrid_results
    )

    # 3. Rerank candidates by relevance
    reranked_candidates = rerank(
        query=query,
        candidates=unique_candidates,
        limit=len(unique_candidates)
    )

    # 4. Create embeddings for diversity comparison
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

    # 5. Select final diverse context with MMR
    final_results = select_with_mmr(
        candidates=reranked_candidates,
        limit=3,
        lambda_value=0.5
    )

    print("\n=== RERANKED CANDIDATES ===")

    for rank, candidate in enumerate(
        reranked_candidates,
        start=1
    ):
        print(
            f"\nRank: {rank}\n"
            f"Document: {candidate['document_id']}\n"
            f"Chunk: {candidate['chunk_id']}\n"
            f"Reranker Score: "
            f"{candidate['reranker_score']:.4f}\n"
            f"Text: {candidate['text']}"
        )

    print("\n=== FINAL MMR CONTEXT ===")

    for rank, candidate in enumerate(
        final_results,
        start=1
    ):
        print(
            f"\nRank: {rank}\n"
            f"Document: {candidate['document_id']}\n"
            f"Chunk: {candidate['chunk_id']}\n"
            f"Normalized Relevance: "
            f"{candidate['relevance']:.4f}\n"
            f"MMR Score: "
            f"{candidate.get('mmr_score', 'first selection')}\n"
            f"Text: {candidate['text']}"
        )


asyncio.run(main())