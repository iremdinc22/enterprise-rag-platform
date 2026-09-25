import asyncio

from src.retrieval.hybrid_retriever import hybrid_search


async def main():
    query = "What is the remote work allowance?"

    acme_results = await hybrid_search(
        query=query,
        tenant_id="acme",
        retrieval_limit=5,
        final_limit=5
    )

    globex_results = await hybrid_search(
        query=query,
        tenant_id="globex",
        retrieval_limit=5,
        final_limit=5
    )

    print("=== ACME HYBRID RESULTS ===")

    for result in acme_results:
        print("Tenant:", result["tenant_id"])
        print("BM25 Rank:", result["bm25_rank"])
        print("Vector Rank:", result["vector_rank"])
        print("RRF Score:", result["rrf_score"])
        print("Text:", result["text"])
        print()

    print("=== GLOBEX HYBRID RESULTS ===")

    for result in globex_results:
        print("Tenant:", result["tenant_id"])
        print("BM25 Rank:", result["bm25_rank"])
        print("Vector Rank:", result["vector_rank"])
        print("RRF Score:", result["rrf_score"])
        print("Text:", result["text"])
        print()


if __name__ == "__main__":
    asyncio.run(main())