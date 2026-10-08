from src.auth.models import UserContext
from src.cache.result_cache import (
    cache_result,
    get_cached_result
)
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
    lambda_value=0.5,
    use_cache=True
):
    # Check result cache only when caching is enabled
    if use_cache:
        cached_result = get_cached_result(
            tenant_id=user_context.tenant_id,
            query=query,
            model=model,
            retrieval_limit=retrieval_limit,
            final_limit=final_limit,
            lambda_value=lambda_value
        )

        if cached_result is not None:
            print("RAG result cache: HIT")
            return cached_result

        print("RAG result cache: MISS")

    else:
        print("RAG result cache: BYPASSED")

    # Retrieve relevant contexts
    contexts = await select_context(
        query=query,
        tenant_id=user_context.tenant_id,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    # Build citations from retrieved contexts
    citations = build_citations(contexts)

    # Format citations for the generation model
    citation_context = format_citation_context(
        citations
    )

    # Generate the answer
    generation = await generate_answer(
        query=query,
        context=citation_context,
        model=model
    )

    # Build citation response
    citation_response = build_citation_response(
        citations
    )

    # Return citations only when the answer is supported
    if not generation.answered:
        citation_response = []
    else:
        citation_response = [
            citation
            for citation in citation_response
            if citation["citation_id"]
            in generation.citation_ids
        ]

    result = {
        "answer": generation.answer,
        "citations": citation_response
    }

    # Save result to cache only when caching is enabled
    if use_cache:
        cache_result(
            tenant_id=user_context.tenant_id,
            query=query,
            model=model,
            retrieval_limit=retrieval_limit,
            final_limit=final_limit,
            lambda_value=lambda_value,
            result=result
        )

    return result