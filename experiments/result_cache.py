from src.cache.result_cache import (
    build_result_cache_key,
    cache_result,
    get_cached_result,
    redis_client
)


def main():
    tenant_id = "acme"
    query = "What is the remote work allowance?"
    model = "gpt-5-mini"

    config_a = {
        "retrieval_limit": 5,
        "final_limit": 3,
        "lambda_value": 0.5
    }

    config_b = {
        "retrieval_limit": 5,
        "final_limit": 5,
        "lambda_value": 0.5
    }

    key_a = build_result_cache_key(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config_a
    )

    key_b = build_result_cache_key(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config_b
    )

    redis_client.delete(key_a, key_b)

    print("=== CACHE KEYS ===")
    print("Config A:", key_a)
    print("Config B:", key_b)
    print("Keys identical:", key_a == key_b)

    print("\n=== FIRST LOOKUP: CONFIG A ===")

    cached_result = get_cached_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config_a
    )

    if cached_result is None:
        print("Cache: MISS")

    result = {
        "answer": "The remote work allowance is 500 USD.",
        "citations": []
    }

    cache_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        result=result,
        **config_a
    )

    print("\n=== SECOND LOOKUP: CONFIG A ===")

    cached_result = get_cached_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config_a
    )

    if cached_result is not None:
        print("Cache: HIT")
        print("Result:", cached_result)

    print("\n=== LOOKUP: CONFIG B ===")

    cached_result = get_cached_result(
        tenant_id=tenant_id,
        query=query,
        model=model,
        **config_b
    )

    if cached_result is None:
        print("Cache: MISS")


if __name__ == "__main__":
    main()