import hashlib
import json
import re

import redis
from redis.exceptions import RedisError


from src.config import REDIS_CACHE_URL
from src.identifiers import validate_identifier


redis_client = redis.Redis.from_url(
    REDIS_CACHE_URL,
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

    try:
        cached_value = redis_client.get(key)

    except RedisError:
        print("Result cache unavailable")
        return None

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

    try:
        redis_client.set(
            key,
            json.dumps(result),
            ex=RESULT_CACHE_TTL
        )

    except RedisError:
        print("Result cache write skipped")


def invalidate_tenant_results(tenant_id):
    validate_identifier(tenant_id)

    deleted_count = 0

    try:
        # SCAN only discovers candidates. Ownership comes from the complete
        # existing key grammar, never a tenant prefix or interpolated glob.
        for key in redis_client.scan_iter(match="rag-result:*"):
            if not isinstance(key, str):
                continue
            parts = key.split(":", 2)
            if (len(parts) != 3 or parts[0] != "rag-result" or parts[1] != tenant_id
                    or re.fullmatch(r"[0-9a-f]{64}", parts[2]) is None):
                continue
            deleted_count += redis_client.delete(key)

    except (RedisError, UnicodeError):
        print("Result cache invalidation skipped")
        return 0

    return deleted_count
