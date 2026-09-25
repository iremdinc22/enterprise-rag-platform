from src.cache.result_cache import (
    cache_result,
    get_cached_result,
    invalidate_tenant_results,
    redis_client
)


def main():
    query = "What is the remote work allowance?"
    model = "gpt-5-mini"

    config = {
        "retrieval_limit": 5,
        "final_limit": 3,
        "lambda_value": 0.5
    }

    # Clean previous result-cache entries
    for key in redis_client.scan_iter(
        match="rag-result:acme:*"
    ):
        redis_client.delete(key)

    for key in redis_client.scan_iter(
        match="rag-result:globex:*"
    ):
        redis_client.delete(key)

    cache_result(
        tenant_id="acme",
        query=query,
        model=model,
        result={
            "answer": "500 USD",
            "citations": []
        },
        **config
    )

    cache_result(
        tenant_id="globex",
        query=query,
        model=model,
        result={
            "answer": "2000 USD",
            "citations": []
        },
        **config
    )

    print("=== BEFORE INVALIDATION ===")

    acme_result = get_cached_result(
        tenant_id="acme",
        query=query,
        model=model,
        **config
    )

    globex_result = get_cached_result(
        tenant_id="globex",
        query=query,
        model=model,
        **config
    )

    print("ACME:", acme_result)
    print("GLOBEX:", globex_result)

    print("\n=== INVALIDATE ACME ===")

    deleted_count = invalidate_tenant_results(
        tenant_id="acme"
    )

    print("Deleted keys:", deleted_count)

    print("\n=== AFTER INVALIDATION ===")

    acme_result = get_cached_result(
        tenant_id="acme",
        query=query,
        model=model,
        **config
    )

    globex_result = get_cached_result(
        tenant_id="globex",
        query=query,
        model=model,
        **config
    )

    print("ACME:", acme_result)
    print("GLOBEX:", globex_result)


if __name__ == "__main__":
    main()