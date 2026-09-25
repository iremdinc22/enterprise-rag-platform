import asyncio

from src.auth.models import Role, UserContext
from src.cache.result_cache import (
    build_result_cache_key,
    redis_client
)
from src.rag.pipeline import run_rag


async def main():
    query = "What is the remote work allowance?"
    model = "gpt-5-mini"

    user_context = UserContext(
        user_id="user-001",
        tenant_id="acme",
        role=Role.EMPLOYEE
    )

    retrieval_limit = 5
    final_limit = 3
    lambda_value = 0.5

    key = build_result_cache_key(
        tenant_id=user_context.tenant_id,
        query=query,
        model=model,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    # Ensure the first request starts with a cache miss
    redis_client.delete(key)

    print("=== FIRST RAG CALL ===")

    first_result = await run_rag(
        query=query,
        model=model,
        user_context=user_context,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    print("Answer:", first_result["answer"])
    print("Citations:", first_result["citations"])

    print("\n=== SECOND RAG CALL ===")

    second_result = await run_rag(
        query=query,
        model=model,
        user_context=user_context,
        retrieval_limit=retrieval_limit,
        final_limit=final_limit,
        lambda_value=lambda_value
    )

    print("Answer:", second_result["answer"])
    print("Citations:", second_result["citations"])

    print("\n=== COMPARISON ===")
    print(
        "Results identical:",
        first_result == second_result
    )


if __name__ == "__main__":
    asyncio.run(main())