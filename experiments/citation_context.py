import asyncio

from src.citations.citation_builder import build_citations
from src.citations.context_formatter import format_citation_context
from src.retrieval.context_selector import select_context


async def main():
    query = (
        "How should employees securely access "
        "company systems remotely?"
    )

    contexts = await select_context(
        query=query,
        retrieval_limit=5,
        final_limit=3,
        lambda_value=0.5
    )

    citations = build_citations(contexts)

    formatted_context = format_citation_context(
        citations
    )

    print("\n=== LLM-READY CITATION CONTEXT ===\n")
    print(formatted_context)


asyncio.run(main())