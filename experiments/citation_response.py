import asyncio
from pprint import pprint

from src.citations.citation_builder import (
    build_citations,
    build_citation_response
)
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

    citation_response = build_citation_response(
        citations
    )

    print("\n=== CITATION API RESPONSE ===\n")
    pprint(citation_response)


asyncio.run(main())