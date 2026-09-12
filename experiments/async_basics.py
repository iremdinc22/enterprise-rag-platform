import asyncio
import time


async def create_embedding_fake_async(document_id):
    print(f"{document_id}: embedding started")

    # Simulate waiting for an asynchronous API response
    await asyncio.sleep(2)

    print(f"{document_id}: embedding finished")


async def main():
    start = time.perf_counter()

    await asyncio.gather(
        create_embedding_fake_async("document-1"),
        create_embedding_fake_async("document-2"),
        create_embedding_fake_async("document-3")
    )

    elapsed = time.perf_counter() - start

    print(f"\nAsync total time: {elapsed:.2f} seconds")


asyncio.run(main())