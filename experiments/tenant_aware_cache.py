import hashlib
import json

import redis


redis_client = redis.Redis(
    host="localhost",
    port=6381,
    db=2,
    decode_responses=True
)


def build_cache_key(
    tenant_id,
    query
):
    query_hash = hashlib.sha256(
        query.encode("utf-8")
    ).hexdigest()

    return (
        f"rag-result:"
        f"{tenant_id}:"
        f"{query_hash}"
    )


def main():
    query = "What is the remote work allowance?"

    acme_result = {
        "tenant_id": "acme",
        "answer": "The remote work allowance is 500 USD."
    }

    globex_result = {
        "tenant_id": "globex",
        "answer": "The remote work allowance is 2000 USD."
    }

    acme_key = build_cache_key(
        tenant_id="acme",
        query=query
    )

    globex_key = build_cache_key(
        tenant_id="globex",
        query=query
    )

    redis_client.delete(
        acme_key,
        globex_key
    )

    print("=== CACHE KEYS ===")
    print("ACME:", acme_key)
    print("GLOBEX:", globex_key)

    print("\nKeys identical:", acme_key == globex_key)

    print("\n=== ACME REQUEST ===")

    cached_result = redis_client.get(acme_key)

    if cached_result is None:
        print("Cache: MISS")

        redis_client.set(
            acme_key,
            json.dumps(acme_result),
            ex=3600
        )

        print("Stored:", acme_result)

    print("\n=== GLOBEX REQUEST ===")

    cached_result = redis_client.get(globex_key)

    if cached_result is None:
        print("Cache: MISS")

        redis_client.set(
            globex_key,
            json.dumps(globex_result),
            ex=3600
        )

        print("Stored:", globex_result)

    print("\n=== SECOND ACME REQUEST ===")

    cached_result = redis_client.get(acme_key)

    if cached_result is not None:
        print("Cache: HIT")
        print("Result:", json.loads(cached_result))

    print("\n=== SECOND GLOBEX REQUEST ===")

    cached_result = redis_client.get(globex_key)

    if cached_result is not None:
        print("Cache: HIT")
        print("Result:", json.loads(cached_result))


if __name__ == "__main__":
    main()