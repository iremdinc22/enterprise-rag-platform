import asyncio

from src.retrieval.bm25_retriever import search_bm25


async def main():
    query = "remote work allowance"

    acme_results = await search_bm25(
        query=query,
        tenant_id="acme",
        limit=5
    )

    globex_results = await search_bm25(
        query=query,
        tenant_id="globex",
        limit=5
    )

    print("=== ACME BM25 RESULTS ===")

    for result in acme_results:
        print(
            "Tenant:",
            result["tenant_id"]
        )
        print(
            "Score:",
            result["score"]
        )
        print(
            "Text:",
            result["text"]
        )
        print()

    print("=== GLOBEX BM25 RESULTS ===")

    for result in globex_results:
        print(
            "Tenant:",
            result["tenant_id"]
        )
        print(
            "Score:",
            result["score"]
        )
        print(
            "Text:",
            result["text"]
        )
        print()


if __name__ == "__main__":
    asyncio.run(main())