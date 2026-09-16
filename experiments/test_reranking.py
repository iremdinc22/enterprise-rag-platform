import asyncio

from src.retrieval.hybrid_retriever import hybrid_search
from src.retrieval.reranker import rerank
from src.retrieval.deduplicator import deduplicate_exact


async def main():
    query = "What is the parental leave policy?"

    # Retrieve candidates using hybrid search
    candidates = await hybrid_search(
        query=query,
        retrieval_limit=5,
        final_limit=5,
        rrf_k=60
    )

    # Remove exact duplicates before expensive reranking
    unique_candidates = deduplicate_exact(
        candidates
    )

    # Rerank unique candidates and select Final Top-K
    final_results = rerank(
        query=query,
        candidates=unique_candidates,
        limit=3
    )

    print("\n=== HYBRID CANDIDATES (RRF) ===")

    for rank, candidate in enumerate(candidates, start=1):
        print(
            f"\nRank: {rank}\n"
            f"Document: {candidate['document_id']}\n"
            f"Chunk: {candidate['chunk_id']}\n"
            f"RRF Score: {candidate['rrf_score']:.6f}\n"
            f"Text: {candidate['text']}"
        )

    print("\n=== UNIQUE CANDIDATES ===")

    for rank, candidate in enumerate(
        unique_candidates,
        start=1
    ):
        print(
            f"\nRank: {rank}\n"
            f"Document: {candidate['document_id']}\n"
            f"Chunk: {candidate['chunk_id']}\n"
            f"RRF Score: {candidate['rrf_score']:.6f}\n"
            f"Text: {candidate['text']}"
        )

    print("\n=== FINAL RESULTS (RERANKED) ===")

    for rank, result in enumerate(final_results, start=1):
        print(
            f"\nRank: {rank}\n"
            f"Document: {result['document_id']}\n"
            f"Chunk: {result['chunk_id']}\n"
            f"RRF Score: {result['rrf_score']:.6f}\n"
            f"Reranker Score: {result['reranker_score']:.4f}\n"
            f"Text: {result['text']}"
        )


asyncio.run(main())