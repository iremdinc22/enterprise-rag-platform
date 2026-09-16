import asyncio

from src.citations.citation_builder import build_citations
from src.retrieval.context_selector import select_context


async def main():
    query = "How should employees securely access company systems remotely?"

    contexts = await select_context(
        query=query,
        retrieval_limit=5,
        final_limit=3,
        lambda_value=0.5
    )

    citations = build_citations(contexts)

    print("\n=== CITATIONS ===")

    for citation in citations:
        print(
            f"\n[{citation['citation_id']}]\n"
            f"Document ID: {citation['document_id']}\n"
            f"Filename: {citation['filename']}\n"
            f"Page: {citation['page']}\n"
            f"Chunk ID: {citation['chunk_id']}\n"
            f"Text: {citation['text']}"
        )


asyncio.run(main())