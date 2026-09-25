import asyncio

from src.auth.models import Role, UserContext
from src.rag.pipeline import run_rag


async def main():
    query = "What is the remote work allowance?"

    acme_user = UserContext(
        user_id="acme-user-123",
        tenant_id="acme",
        role=Role.EMPLOYEE
    )

    globex_user = UserContext(
        user_id="globex-user-456",
        tenant_id="globex",
        role=Role.EMPLOYEE
    )

    acme_response = await run_rag(
        query=query,
        model="gpt-5-mini",
        user_context=acme_user,
        retrieval_limit=5,
        final_limit=3
    )

    globex_response = await run_rag(
        query=query,
        model="gpt-5-mini",
        user_context=globex_user,
        retrieval_limit=5,
        final_limit=3
    )

    print("=== ACME RAG RESPONSE ===")
    print("User:", acme_user.user_id)
    print("Tenant:", acme_user.tenant_id)
    print("Answer:", acme_response["answer"])
    print("Citations:", acme_response["citations"])

    print("\n=== GLOBEX RAG RESPONSE ===")
    print("User:", globex_user.user_id)
    print("Tenant:", globex_user.tenant_id)
    print("Answer:", globex_response["answer"])
    print("Citations:", globex_response["citations"])


if __name__ == "__main__":
    asyncio.run(main())