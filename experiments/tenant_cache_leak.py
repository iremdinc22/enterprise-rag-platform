import hashlib
import json

import redis


redis_client = redis.Redis(
    host="localhost",
    port=6381,
    db=2,
    decode_responses=True
)


def build_bad_cache_key(query):
    query_hash = hashlib.sha256(
        query.encode("utf-8")
    ).hexdigest()

    return f"rag-result:{query_hash}"


def main():
    query = "What is the remote work allowance?"

    acme_result = {
        "tenant_id": "acme",
        "answer": "The remote work allowance is 500 USD."
    }

    key = build_bad_cache_key(query)

    redis_client.delete(key)

    print("=== CACHE KEY ===")
    print(key)

    print("\n=== ACME REQUEST ===")

    cached_result = redis_client.get(key)

    if cached_result is None:
        print("Cache: MISS")

        redis_client.set(
            key,
            json.dumps(acme_result),
            ex=3600
        )

        print("Stored ACME result:")
        print(acme_result)

    print("\n=== GLOBEX REQUEST ===")

    cached_result = redis_client.get(key)

    if cached_result is not None:
        print("Cache: HIT")

        result = json.loads(cached_result)

        print("Requested tenant: globex")
        print("Cached tenant:", result["tenant_id"])
        print("Answer:", result["answer"])


if __name__ == "__main__":
    main()