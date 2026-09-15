import asyncio

from src.vector_store.qdrant_store import (
    client,
    COLLECTION_NAME,
    create_collection
)


async def main():
    if await client.collection_exists(COLLECTION_NAME):
        await client.delete_collection(COLLECTION_NAME)
        print("Existing collection deleted")

    await create_collection()
    print("Collection recreated")


asyncio.run(main())