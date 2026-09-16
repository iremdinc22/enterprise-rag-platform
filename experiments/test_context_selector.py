import asyncio

from src.retrieval.context_selector import select_context


async def main():
    query = "remote employee network access"

    lambda_values = [0.2, 0.5, 0.8, 1.0]

    for lambda_value in lambda_values:
        results = await select_context(
            query=query,
            retrieval_limit=5,
            final_limit=3,
            lambda_value=lambda_value
        )

        print(
            f"\n{'=' * 60}\n"
            f"LAMBDA = {lambda_value}\n"
            f"{'=' * 60}"
        )

        for rank, result in enumerate(results, start=1):
            print(
                f"\nRank: {rank}\n"
                f"Document: {result['document_id']}\n"
                f"Chunk: {result['chunk_id']}\n"
                f"Reranker Score: "
                f"{result['reranker_score']:.4f}\n"
                f"Normalized Relevance: "
                f"{result['relevance']:.4f}\n"
                f"MMR Score: "
                f"{result.get('mmr_score', 'first selection')}\n"
                f"Text: {result['text']}"
            )


asyncio.run(main())