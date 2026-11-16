"""Explicit, idempotent creation of the configured collection; no ingestion."""
import asyncio

from src.vector_store.qdrant_store import client, create_collection


async def main():
    try:
        await create_collection()
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
