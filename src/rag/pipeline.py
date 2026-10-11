from opentelemetry.trace import Status, StatusCode

from src.auth.models import UserContext
from src.cache.result_cache import (
    cache_result,
    get_cached_result,
)
from src.citations.citation_builder import (
    build_citations,
    build_citation_response,
)
from src.citations.context_formatter import format_citation_context
from src.generation.generator import generate_answer
from src.observability.tracing import get_tracer
from src.retrieval.context_selector import select_context


async def run_rag(
    query,
    model,
    user_context: UserContext,
    retrieval_limit=5,
    final_limit=3,
    lambda_value=0.5,
    use_cache=True,
):
    tracer = get_tracer()

    with tracer.start_as_current_span("rag.query") as root_span:
        root_span.set_attribute("rag.model", model)
        root_span.set_attribute("rag.retrieval_limit", retrieval_limit)
        root_span.set_attribute("rag.final_limit", final_limit)
        root_span.set_attribute("rag.cache_enabled", use_cache)

        try:
            # Check result cache
            if use_cache:
                with tracer.start_as_current_span("cache.lookup") as span:
                    cached_result = get_cached_result(
                        tenant_id=user_context.tenant_id,
                        query=query,
                        model=model,
                        retrieval_limit=retrieval_limit,
                        final_limit=final_limit,
                        lambda_value=lambda_value,
                    )

                    if cached_result is not None:
                        span.set_attribute("cache.status", "HIT")
                        root_span.set_attribute("rag.cache_status", "HIT")
                        print("RAG result cache: HIT")
                        return cached_result

                    span.set_attribute("cache.status", "MISS")
                    root_span.set_attribute("rag.cache_status", "MISS")
                    print("RAG result cache: MISS")

            else:
                root_span.set_attribute("rag.cache_status", "BYPASSED")
                print("RAG result cache: BYPASSED")

            # Retrieve relevant contexts
            with tracer.start_as_current_span(
                "retrieval.context_selection"
            ) as span:
                contexts = await select_context(
                    query=query,
                    tenant_id=user_context.tenant_id,
                    retrieval_limit=retrieval_limit,
                    final_limit=final_limit,
                    lambda_value=lambda_value,
                )
                span.set_attribute("retrieval.context_count", len(contexts))

            # Build citations
            with tracer.start_as_current_span("citations.build") as span:
                citations = build_citations(contexts)
                citation_context = format_citation_context(citations)
                span.set_attribute("citations.group_count", len(citations))

            # Generate answer
            with tracer.start_as_current_span("generation.answer") as span:
                generation = await generate_answer(
                    query=query,
                    context=citation_context,
                    model=model,
                )
                span.set_attribute("generation.answered", generation.answered)
                span.set_attribute(
                    "generation.citation_count",
                    len(generation.citation_ids),
                )

            # Build citation response
            citation_response = build_citation_response(citations)

            if not generation.answered:
                citation_response = []
            else:
                citation_response = [
                    citation
                    for citation in citation_response
                    if citation["citation_id"] in generation.citation_ids
                ]

            result = {
                "answer": generation.answer,
                "citations": citation_response,
            }

            # Save result to cache
            if use_cache:
                with tracer.start_as_current_span("cache.write"):
                    cache_result(
                        tenant_id=user_context.tenant_id,
                        query=query,
                        model=model,
                        retrieval_limit=retrieval_limit,
                        final_limit=final_limit,
                        lambda_value=lambda_value,
                        result=result,
                    )

            root_span.set_attribute("rag.answered", generation.answered)
            root_span.set_attribute(
                "rag.returned_citation_count",
                len(citation_response),
            )

            return result

        except Exception as exc:
            root_span.record_exception(exc)
            root_span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
