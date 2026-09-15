import asyncio

from src.ingestion.embedder import create_embedding
from src.retrieval.bm25_retriever import search_bm25
from src.vector_store.qdrant_store import search_chunks


async def main():
    query = "vacation time"
    limit = 3

    # BM25 retrieval
    bm25_results = await search_bm25(
        query=query,
        limit=limit
    )

    # Vector retrieval
    query_embedding = await create_embedding(query)

    vector_results = await search_chunks(
        query_vector=query_embedding,
        limit=limit
    )

    print("\n=== BM25 RESULTS ===")

    for rank, result in enumerate(bm25_results, start=1):
        print(
            f"\nRank: {rank}\n"
            f"Score: {result['score']:.4f}\n"
            f"Document: {result['document_id']}\n"
            f"Chunk: {result['chunk_id']}\n"
            f"Page: {result['page']}\n"
            f"Text: {result['text']}"
        )

    print("\n=== VECTOR RESULTS ===")

    for rank, result in enumerate(vector_results, start=1):
        payload = result.payload

        print(
            f"\nRank: {rank}\n"
            f"Score: {result.score:.4f}\n"
            f"Document: {payload['document_id']}\n"
            f"Chunk: {payload['chunk_id']}\n"
            f"Page: {payload['page']}\n"
            f"Text: {payload['text']}"
        )


asyncio.run(main())