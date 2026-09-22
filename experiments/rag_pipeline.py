import asyncio
from pprint import pprint

from src.rag.pipeline import run_rag


async def main():
    query = "What is the company's parental leave policy?"

    result = await run_rag(
        query=query,
        model="gpt-5-mini",
        retrieval_limit=5,
        final_limit=3,
        lambda_value=0.5
    )

    print("\n=== RAG RESPONSE ===\n")
    pprint(result)


asyncio.run(main())