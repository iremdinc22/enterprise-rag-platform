import asyncio

from src.retrieval.hybrid_retriever import hybrid_search


async def main():
    query = "remote employee network access"

    results = await hybrid_search(
        query=query,
        retrieval_limit=5,
        final_limit=3,
        rrf_k=60
    )

    print(f"\nQuery: {query}")
    print("\n=== HYBRID RESULTS ===")

    for rank, result in enumerate(results, start=1):
        print(
            f"\nFinal Rank: {rank}\n"
            f"RRF Score: {result['rrf_score']:.6f}\n"
            f"Document: {result['document_id']}\n"
            f"Chunk: {result['chunk_id']}\n"
            f"Page: {result['page']}\n"
            f"BM25 Rank: {result['bm25_rank']}\n"
            f"Vector Rank: {result['vector_rank']}\n"
            f"BM25 Score: {result['bm25_score']}\n"
            f"Vector Score: {result['vector_score']}\n"
            f"Text: {result['text']}"
        )


asyncio.run(main())