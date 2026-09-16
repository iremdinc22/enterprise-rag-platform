import asyncio

from src.retrieval.context_selector import select_context


async def main():
    query = "How should employees securely access company systems remotely?"

    results = await select_context(
        query=query,
        retrieval_limit=5,
        final_limit=3,
        lambda_value=0.5
    )

    print("\n=== FINAL CONTEXT WITH PROVENANCE ===")

    for rank, result in enumerate(results, start=1):
        print(
            f"\nSource: {rank}\n"
            f"Document ID: {result['document_id']}\n"
            f"Filename: {result['filename']}\n"
            f"Page: {result['page']}\n"
            f"Chunk ID: {result['chunk_id']}\n"
            f"Text: {result['text']}"
        )


asyncio.run(main())