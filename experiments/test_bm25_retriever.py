import asyncio

from src.retrieval.bm25_retriever import search_bm25


async def main():
    results = await search_bm25(
        query="vacation time",
        limit=3
    )

    for rank, result in enumerate(results, start=1):
        print(
            f"\nRank: {rank}\n"
            f"Score: {result['score']:.4f}\n"
            f"Document: {result['document_id']}\n"
            f"Chunk: {result['chunk_id']}\n"
            f"Page: {result['page']}\n"
            f"Text: {result['text']}"
        )


asyncio.run(main())