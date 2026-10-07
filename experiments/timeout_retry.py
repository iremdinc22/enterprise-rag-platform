import asyncio


async def slow_operation(attempt):
    print(f"Operation started - attempt {attempt}")

    if attempt < 3:
        await asyncio.sleep(5)

    else:
        await asyncio.sleep(0.5)

    return "Operation completed"


async def main():
    max_attempts = 4
    timeout_seconds = 2
    base_delay = 1

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                slow_operation(attempt),
                timeout=timeout_seconds
            )

            print("Result:", result)
            return

        except asyncio.TimeoutError:
            print(f"Attempt {attempt} timed out")

            if attempt == max_attempts:
                print("Retries exhausted")
                raise

            delay = base_delay * (2 ** (attempt - 1))

            print(f"Retrying in {delay} seconds...\n")

            await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())