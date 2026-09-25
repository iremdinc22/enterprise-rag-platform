import asyncio

from src.auth.models import Role, UserContext
from src.rag.pipeline import run_rag
from src.retrieval.context_selector import select_context


async def main():
    query = "What is GLOBEX's remote work allowance?"

    # The authenticated user belongs to ACME, not GLOBEX
    acme_user = UserContext(
        user_id="acme-user-123",
        tenant_id="acme",
        role=Role.EMPLOYEE
    )

    # Inspect retrieved context first
    contexts = await select_context(
        query=query,
        tenant_id=acme_user.tenant_id,
        retrieval_limit=5,
        final_limit=3
    )

    print("=== RETRIEVED CONTEXT ===")

    for context in contexts:
        print("Tenant:", context["tenant_id"])
        print("File:", context["filename"])
        print("Text:", context["text"])
        print()

    # Run the complete RAG pipeline
    response = await run_rag(
        query=query,
        model="gpt-5-mini",
        user_context=acme_user,
        retrieval_limit=5,
        final_limit=3
    )

    print("=== FINAL RESPONSE ===")
    print("Authenticated tenant:", acme_user.tenant_id)
    print("Question:", query)
    print("Answer:", response["answer"])
    print("Citations:", response["citations"])


if __name__ == "__main__":
    asyncio.run(main())