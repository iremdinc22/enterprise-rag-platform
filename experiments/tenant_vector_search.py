import asyncio

from src.ingestion.embedder import create_embedding
from src.vector_store.qdrant_store import search_chunks


async def main():
    query = "What is the remote work allowance?"

    query_embedding = await create_embedding(query)

    acme_results = await search_chunks(
        query_vector=query_embedding,
        tenant_id="acme",
        limit=5
    )

    globex_results = await search_chunks(
        query_vector=query_embedding,
        tenant_id="globex",
        limit=5
    )

    print("=== ACME VECTOR RESULTS ===")

    for result in acme_results:
        print(
            "Tenant:",
            result.payload["tenant_id"]
        )
        print(
            "Text:",
            result.payload["text"]
        )
        print()

    print("=== GLOBEX VECTOR RESULTS ===")

    for result in globex_results:
        print(
            "Tenant:",
            result.payload["tenant_id"]
        )
        print(
            "Text:",
            result.payload["text"]
        )
        print()


if __name__ == "__main__":
    asyncio.run(main())