query = "How do employees securely access company systems remotely?"

candidates = [
    {
        "id": "chunk-1",
        "text": "Remote employees must connect to company systems using the corporate VPN.",
        "reranker_score": 8.7
    },
    {
        "id": "chunk-2",
        "text": "Employees working remotely are required to use the approved corporate VPN.",
        "reranker_score": 8.5
    },
    {
        "id": "chunk-3",
        "text": "The corporate VPN must be used when accessing internal systems from outside the office.",
        "reranker_score": 8.2
    },
    {
        "id": "chunk-4",
        "text": "Employees may work remotely up to two days per week.",
        "reranker_score": 7.8
    },
    {
        "id": "chunk-5",
        "text": "Employees should avoid public or unsecured networks while working remotely.",
        "reranker_score": 7.6
    }
]

top_k = candidates[:3]

print("=== TOP-3 BY RELEVANCE ===")

for candidate in top_k:
    print(
        f"\n{candidate['id']}\n"
        f"Score: {candidate['reranker_score']}\n"
        f"Text: {candidate['text']}"
    )