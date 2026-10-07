import redis


def main():
    broken_cache = redis.Redis(
        host="localhost",
        port=9999,  # Nothing is running here
        db=2,
        decode_responses=True
    )

    print("Trying to read from cache...")

    value = broken_cache.get("test-key")

    print("Cache value:", value)


if __name__ == "__main__":
    main()