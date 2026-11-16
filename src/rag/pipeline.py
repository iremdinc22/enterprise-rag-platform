
from opentelemetry.trace import Status, StatusCode

from src.auth.models import UserContext
from src.cache.result_cache import (
    cache_result,
    get_cached_result,
)
from src.citations.citation_builder import (
    authorized_citation_ids,
    build_citations,
    build_citation_response,
)
from src.citations.context_formatter import format_citation_context
from src.generation.generator import (
    ABSTENTION_MESSAGE,
    extract_citation_ids,
    generate_answer,
    validate_generated_answer,
)
from src.observability.tracing import get_tracer
from src.observability.usage_tracker import (
    start_query_usage,
    get_query_usage,
    reset_query_usage,
)
from src.retrieval.context_selector import select_context
from src.rag.execution import run_blocking


def validate_citation_response(result):
    """Require every inline reference to map to an actual returned source group."""
    inline_ids = extract_citation_ids(result["answer"])
    if (inline_ids != authorized_citation_ids(result["citations"])
            or (not inline_ids and result["answer"].strip() != ABSTENTION_MESSAGE)):
        raise ValueError("Answer citations do not match returned citation groups")


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

    # Start a request-scoped usage collector.
    usage_token = start_query_usage()

    try:
        with tracer.start_as_current_span(
            "rag.query",
            record_exception=False,
            set_status_on_exception=False,
        ) as root_span:
            root_span.set_attribute("rag.model", model)
            root_span.set_attribute(
                "rag.retrieval_limit",
                retrieval_limit,
            )
            root_span.set_attribute(
                "rag.final_limit",
                final_limit,
            )
            root_span.set_attribute(
                "rag.cache_enabled",
                use_cache,
            )

            try:
                # Check result cache
                if use_cache:
                    with tracer.start_as_current_span(
                        "cache.lookup",
                        record_exception=False,
                        set_status_on_exception=False,
                    ) as span:
                        cached_result = await run_blocking(
                            get_cached_result,
                            tenant_id=user_context.tenant_id,
                            query=query,
                            model=model,
                            retrieval_limit=retrieval_limit,
                            final_limit=final_limit,
                            lambda_value=lambda_value,
                        )

                        if cached_result is not None:
                            validate_citation_response(cached_result)
                            span.set_attribute(
                                "cache.status",
                                "HIT",
                            )
                            root_span.set_attribute(
                                "rag.cache_status",
                                "HIT",
                            )
                            print("RAG result cache: HIT")

                            return cached_result

                        span.set_attribute(
                            "cache.status",
                            "MISS",
                        )
                        root_span.set_attribute(
                            "rag.cache_status",
                            "MISS",
                        )
                        print("RAG result cache: MISS")

                else:
                    root_span.set_attribute(
                        "rag.cache_status",
                        "BYPASSED",
                    )
                    print("RAG result cache: BYPASSED")

                # Retrieve relevant contexts
                with tracer.start_as_current_span(
                    "retrieval.context_selection",
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span:
                    contexts = await select_context(
                        query=query,
                        tenant_id=user_context.tenant_id,
                        retrieval_limit=retrieval_limit,
                        final_limit=final_limit,
                        lambda_value=lambda_value,
                    )

                    span.set_attribute(
                        "retrieval.context_count",
                        len(contexts),
                    )

                # Build citations
                with tracer.start_as_current_span(
                    "citations.build",
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span:
                    citations = build_citations(contexts)

                    citation_context = format_citation_context(
                        citations
                    )

                    span.set_attribute(
                        "citations.group_count",
                        len(citations),
                    )

                # Generate answer
                with tracer.start_as_current_span(
                    "generation.answer",
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span:
                    generation = await generate_answer(
                        query=query,
                        context=citation_context,
                        model=model,
                        citations=citations,
                    )

                    if not validate_generated_answer(generation, authorized_citation_ids(citations)):
                        raise ValueError("Generation failed citation consistency validation")

                    span.set_attribute(
                        "generation.answered",
                        generation.answered,
                    )
                    span.set_attribute(
                        "generation.citation_count",
                        len(generation.citation_ids),
                    )

                # Build citation response
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

                result = {
                    "answer": generation.answer,
                    "citations": citation_response,
                }
                validate_citation_response(result)

                # Save result to cache
                if use_cache:
                    with tracer.start_as_current_span(
                        "cache.write",
                        record_exception=False,
                        set_status_on_exception=False,
                    ):
                        await run_blocking(
                            cache_result,
                            tenant_id=user_context.tenant_id,
                            query=query,
                            model=model,
                            retrieval_limit=retrieval_limit,
                            final_limit=final_limit,
                            lambda_value=lambda_value,
                            result=result,
                        )

                root_span.set_attribute(
                    "rag.answered",
                    generation.answered,
                )
                root_span.set_attribute(
                    "rag.returned_citation_count",
                    len(citation_response),
                )

                return result

            except Exception:
                root_span.set_status(
                    Status(StatusCode.ERROR, "Query processing failed")
                )
                raise

            finally:
                # Aggregate usage from all API calls,
                # including generation retries.
                usage = get_query_usage()

                if usage is not None:
                    root_span.set_attribute(
                        "rag.generation_attempts",
                        usage.generation_attempts,
                    )
                    root_span.set_attribute(
                        "rag.generation_input_tokens",
                        usage.generation_input_tokens,
                    )
                    root_span.set_attribute(
                        "rag.generation_cached_input_tokens",
                        usage.generation_cached_input_tokens,
                    )
                    root_span.set_attribute(
                        "rag.generation_output_tokens",
                        usage.generation_output_tokens,
                    )
                    root_span.set_attribute(
                        "rag.embedding_api_calls",
                        usage.embedding_api_calls,
                    )
                    root_span.set_attribute(
                        "rag.embedding_cache_hits",
                        usage.embedding_cache_hits,
                    )
                    root_span.set_attribute(
                        "rag.embedding_input_tokens",
                        usage.embedding_input_tokens,
                    )
                    root_span.set_attribute(
                        "rag.total_api_tokens",
                        usage.total_api_tokens,
                    )
                    root_span.set_attribute(
                        "rag.generation_cost_usd",
                        float(usage.generation_cost_usd),
                    )
                    root_span.set_attribute(
                        "rag.embedding_cost_usd",
                        float(usage.embedding_cost_usd),
                    )
                    root_span.set_attribute(
                        "rag.total_cost_usd",
                        float(usage.total_cost_usd),
                    )

    finally:
        # Restore the previous async context,
        # including when the result cache returns early.
        reset_query_usage(usage_token)
