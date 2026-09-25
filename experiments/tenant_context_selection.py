import asyncio

from src.retrieval.context_selector import select_context


async def main():
    query = "What is the remote work allowance?"

    acme_contexts = await select_context(
        query=query,
        tenant_id="acme",
        retrieval_limit=5,
        final_limit=3
    )

    globex_contexts = await select_context(
        query=query,
        tenant_id="globex",
        retrieval_limit=5,
        final_limit=3
    )

    print("=== ACME SELECTED CONTEXT ===")

    for context in acme_contexts:
        print("Tenant:", context["tenant_id"])
        print("Reranker Score:", context["reranker_score"])
        print("MMR Score:", context.get("mmr_score"))
        print("Text:", context["text"])
        print()

    print("=== GLOBEX SELECTED CONTEXT ===")

    for context in globex_contexts:
        print("Tenant:", context["tenant_id"])
        print("Reranker Score:", context["reranker_score"])
        print("MMR Score:", context.get("mmr_score"))
        print("Text:", context["text"])
        print()


if __name__ == "__main__":
    asyncio.run(main())