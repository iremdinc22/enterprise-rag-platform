import asyncio


async def slow_operation():
    print("Operation started")

    await asyncio.sleep(5)

    return "Operation completed"


async def main():
    try:
        result = await asyncio.wait_for(
            slow_operation(),
            timeout=2
        )

        print("Result:", result)

    except asyncio.TimeoutError:
        print("Operation timed out")


if __name__ == "__main__":
    asyncio.run(main())