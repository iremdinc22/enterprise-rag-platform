from src.citations.citation_builder import build_citations
from src.retrieval.deduplicator import deduplicate_exact


candidates = [
    {
        "document_id": "handbook",
        "filename": "employee-handbook.pdf",
        "page": 1,
        "chunk_id": "chunk-001",
        "text": "Employees receive 20 days of annual leave."
    },
    {
        "document_id": "hr-policy",
        "filename": "hr-policy.pdf",
        "page": 4,
        "chunk_id": "chunk-007",
        "text": "Employees receive 20 days of annual leave."
    },
    {
        "document_id": "remote-policy",
        "filename": "remote-policy.pdf",
        "page": 2,
        "chunk_id": "chunk-003",
        "text": "Employees must use the corporate VPN."
    }
]


unique_contexts = deduplicate_exact(candidates)

citations = build_citations(unique_contexts)

print("\n=== CITATIONS WITH PROVENANCE ===")

for citation in citations:
    print(
        f"\n[{citation['citation_id']}] "
        f"{citation['text']}"
    )

    for source in citation["sources"]:
        print(
            f"  - {source['filename']} "
            f"| page={source['page']} "
            f"| chunk={source['chunk_id']}"
        )