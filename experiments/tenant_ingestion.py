import asyncio

from src.ingestion.pipeline import ingest_document


async def main():
    acme_chunks = await ingest_document(
        file_path="data/acme-policy.pdf",
        document_id="remote-work-policy",
        tenant_id="acme"
    )

    globex_chunks = await ingest_document(
        file_path="data/globex-policy.pdf",
        document_id="remote-work-policy",
        tenant_id="globex"
    )

    employee_chunks = await ingest_document(
        file_path="data/employee-handbook.pdf",
        document_id="employee-handbook",
        tenant_id="acme"
    )

    globex_handbook_chunks = await ingest_document(
        file_path="data/globex-handbook.pdf",
        document_id="employee-handbook",
        tenant_id="globex"
    )

    print("\n=== GLOBEX EMPLOYEE HANDBOOK ===")
    for chunk in globex_handbook_chunks:
        print(
            chunk["tenant_id"],
            chunk["document_id"],
            chunk["chunk_id"],
            chunk["text"]
        )

    print("=== ACME ===")
    for chunk in acme_chunks:
        print(
            chunk["tenant_id"],
            chunk["document_id"],
            chunk["chunk_id"],
            chunk["text"]
        )

    print("\n=== GLOBEX ===")
    for chunk in globex_chunks:
        print(
            chunk["tenant_id"],
            chunk["document_id"],
            chunk["chunk_id"],
            chunk["text"]
        )

    print("\n=== ACME EMPLOYEE HANDBOOK ===")
    for chunk in employee_chunks:
        print(
            chunk["tenant_id"],
            chunk["document_id"],
            chunk["chunk_id"],
            chunk["text"]
        )


if __name__ == "__main__":
    asyncio.run(main())