import hashlib
import json

import redis


redis_client = redis.Redis(
    host="localhost",
    port=6381,
    db=2,
    decode_responses=True
)


RESULT_CACHE_TTL = 1800


def build_result_cache_key(
    tenant_id,
    query,
    model,
    retrieval_limit,
    final_limit,
    lambda_value
):
    cache_identity = {
        "query": query,
        "model": model,
        "retrieval_limit": retrieval_limit,
        "final_limit": final_limit,
        "lambda_value": lambda_value
    }

    serialized_identity = json.dumps(
        cache_identity,
        sort_keys=True
    )

    identity_hash = hashlib.sha256(
        serialized_identity.encode("utf-8")
    ).hexdigest()

    return (
        f"rag-result:"
        f"{tenant_id}:"
        f"{identity_hash}"
    )


def get_cached_result(
    tenant_id,
    query,
    model,
    retrieval_limit,
    final_limit,
    lambda_value
):
    key = build_result_cache_key(
        tenant_id=tenant_id,
        query=query,
        model=model,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    cached_value = redis_client.get(key)

    if cached_value is None:
        return None

    return json.loads(cached_value)


def cache_result(
    tenant_id,
    query,
    model,
    retrieval_limit,
    final_limit,
    lambda_value,
    result
):
    key = build_result_cache_key(
        tenant_id=tenant_id,
        query=query,
        model=model,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    redis_client.set(
        key,
        json.dumps(result),
        ex=RESULT_CACHE_TTL
    )


def invalidate_tenant_results(tenant_id):
    pattern = f"rag-result:{tenant_id}:*"

    deleted_count = 0

    for key in redis_client.scan_iter(match=pattern):
        deleted_count += redis_client.delete(key)

    return deleted_count