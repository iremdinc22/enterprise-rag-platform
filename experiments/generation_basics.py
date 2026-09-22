import asyncio

from src.citations.citation_builder import (
    build_citations,
    build_citation_response
)
from src.citations.context_formatter import format_citation_context
from src.generation.generator import generate_answer
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

    citation_context = format_citation_context(
        citations
    )

    answer = await generate_answer(
        query=query,
        context=citation_context,
        model="gpt-5-mini"
    )

    citation_response = build_citation_response(
        citations
    )

    print("\n=== QUESTION ===\n")
    print(query)

    print("\n=== GENERATED ANSWER ===\n")
    print(answer)

    print("\n=== CITATIONS ===\n")

    for citation in citation_response:
        print(f"[{citation['citation_id']}]")

        for source in citation["sources"]:
            print(
                f"  - {source['filename']} "
                f"| page={source['page']} "
                f"| chunk={source['chunk_id']}"
            )


asyncio.run(main())