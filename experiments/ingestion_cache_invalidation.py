import asyncio

from src.cache.result_cache import (
    cache_result,
    get_cached_result
)
from src.ingestion.pipeline import ingest_document


async def main():
    tenant_id = "acme"
    query = "What is the remote work allowance?"
    model = "gpt-5-mini"

    config = {
        "retrieval_limit": 5,
        "final_limit": 3,
        "lambda_value": 0.5
    }

    # 1. Simulate an existing cached RAG result
    cache_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        result={
            "answer": "500 USD",
            "citations": []
        },
        **config
    )

    print("=== BEFORE INGESTION ===")

    cached_result = get_cached_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config
    )

    print("Cached result:", cached_result)

    # 2. Re-ingest the ACME document
    print("\n=== INGEST DOCUMENT ===")

    chunks = await ingest_document(
        file_path="data/acme-policy.pdf",
        document_id="remote-work-policy",
        tenant_id=tenant_id
    )

    print("Chunks ingested:", len(chunks))

    # 3. Check the result cache again
    print("\n=== AFTER INGESTION ===")

    cached_result = get_cached_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config
    )

    print("Cached result:", cached_result)

    if cached_result is None:
        print("Result: CACHE INVALIDATED")
    else:
        print("Result: CACHE STILL EXISTS")


if __name__ == "__main__":
    asyncio.run(main())