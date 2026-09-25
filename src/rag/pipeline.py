from src.auth.models import UserContext
from src.citations.citation_builder import (
    build_citations,
    build_citation_response
)
from src.citations.context_formatter import format_citation_context
from src.generation.generator import generate_answer
from src.retrieval.context_selector import select_context


async def run_rag(
    query,
    model,
    user_context: UserContext,
    retrieval_limit=5,
    final_limit=3,
    lambda_value=0.5
):
    contexts = await select_context(
        query=query,
        tenant_id=user_context.tenant_id,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    citations = build_citations(contexts)

    citation_context = format_citation_context(
        citations
    )

    generation = await generate_answer(
        query=query,
        context=citation_context,
        model=model
    )

    citation_response = build_citation_response(
        citations
    )

    if not generation.answered:
        citation_response = []
    else:
        citation_response = [
            citation
            for citation in citation_response
            if citation["citation_id"]
            in generation.citation_ids
        ]

    return {
        "answer": generation.answer,
        "citations": citation_response
    }