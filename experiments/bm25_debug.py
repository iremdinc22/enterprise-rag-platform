import asyncio

from src.retrieval.bm25_retriever import (
    build_bm25_index,
    tokenize
)


async def main():
    tenant_id = "acme"
    query = "remote work allowance"

    bm25, chunks = await build_bm25_index(
        tenant_id=tenant_id
    )

    query_tokens = tokenize(query)

    scores = bm25.get_scores(
        query_tokens
    )

    print("Query tokens:", query_tokens)
    print("Number of chunks:", len(chunks))

    for chunk, score in zip(chunks, scores):
        print("\nTenant:", chunk["tenant_id"])
        print("Score:", float(score))
        print("Text:", chunk["text"])


if __name__ == "__main__":
    asyncio.run(main())