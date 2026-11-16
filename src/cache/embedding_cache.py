import hashlib
import json

import redis
from redis.exceptions import RedisError


from src.config import REDIS_CACHE_URL


redis_client = redis.Redis.from_url(
    REDIS_CACHE_URL,
    decode_responses=True
)


EMBEDDING_CACHE_TTL = 3600


def build_embedding_cache_key(
    text,
    model
):
    text_hash = hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    return (
        f"embedding:"
        f"{model}:"
        f"{text_hash}"
    )


def get_cached_embedding(
    text,
    model
):
    key = build_embedding_cache_key(
        text=text,
        model=model
    )

    try:
        cached_value = redis_client.get(key)

    except RedisError:
        print("Embedding cache unavailable")
        return None

    if cached_value is None:
        return None

    return json.loads(cached_value)


def cache_embedding(
    text,
    model,
    embedding
):
    key = build_embedding_cache_key(
        text=text,
        model=model
    )

    try:
        redis_client.set(
            key,
            json.dumps(embedding),
            ex=EMBEDDING_CACHE_TTL
        )

    except RedisError:
        print("Embedding cache write skipped")
