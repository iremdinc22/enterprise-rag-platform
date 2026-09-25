import time

import redis


redis_client = redis.Redis(
    host="localhost",
    port=6381,
    db=2,
    decode_responses=True
)


def main():
    key = "cache:demo:remote-work"
    value = "Remote work allowance is 500 USD."

    # Start with an empty cache entry
    redis_client.delete(key)

    print("=== 1. CACHE MISS ===")

    cached_value = redis_client.get(key)

    print("Value:", cached_value)

    if cached_value is None:
        print("Result: MISS")

    # Store the value for 10 seconds
    redis_client.set(
        key,
        value,
        ex=10
    )

    print("\n=== 2. VALUE CACHED ===")
    print("Value:", redis_client.get(key))
    print("TTL:", redis_client.ttl(key))

    print("\n=== 3. CACHE HIT ===")

    cached_value = redis_client.get(key)

    if cached_value is not None:
        print("Result: HIT")
        print("Value:", cached_value)

    print("\nWaiting for cache expiration...")
    time.sleep(11)

    print("\n=== 4. AFTER TTL ===")
    print("Value:", redis_client.get(key))
    print("TTL:", redis_client.ttl(key))


if __name__ == "__main__":
    main()