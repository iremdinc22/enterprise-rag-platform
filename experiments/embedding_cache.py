from src.cache.embedding_cache import (
    build_embedding_cache_key,
    cache_embedding,
    get_cached_embedding,
    redis_client
)


def main():
    text = "What is the remote work allowance?"
    model = "text-embedding-3-small"

    key = build_embedding_cache_key(
        text=text,
        model=model
    )

    # Ensure a clean experiment
    redis_client.delete(key)

    print("=== CACHE KEY ===")
    print(key)

    print("\n=== FIRST LOOKUP ===")

    cached_embedding = get_cached_embedding(
        text=text,
        model=model
    )

    print("Cached:", cached_embedding)

    if cached_embedding is None:
        print("Result: MISS")

    fake_embedding = [
        0.12,
        -0.34,
        0.56
    ]

    cache_embedding(
        text=text,
        model=model,
        embedding=fake_embedding
    )

    print("\n=== SECOND LOOKUP ===")

    cached_embedding = get_cached_embedding(
        text=text,
        model=model
    )

    print("Result: HIT")
    print("Embedding:", cached_embedding)

    print("\n=== TTL ===")
    print(
        redis_client.ttl(key)
    )


if __name__ == "__main__":
    main()