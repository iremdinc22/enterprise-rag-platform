import asyncio

from src.cache.embedding_cache import (
    build_embedding_cache_key,
    redis_client
)
from src.ingestion.embedder import create_embedding


async def main():
    text = "What is the remote work allowance?"
    model = "text-embedding-3-small"

    key = build_embedding_cache_key(
        text=text,
        model=model
    )

    # Start with an empty cache entry
    redis_client.delete(key)

    print("=== FIRST CALL ===")

    first_embedding = await create_embedding(
        text=text,
        model=model
    )

    print("Embedding dimensions:", len(first_embedding))

    print("\n=== SECOND CALL ===")

    second_embedding = await create_embedding(
        text=text,
        model=model
    )

    print("Embedding dimensions:", len(second_embedding))

    print("\n=== COMPARISON ===")
    print(
        "Embeddings identical:",
        first_embedding == second_embedding
    )


if __name__ == "__main__":
    asyncio.run(main())