from sentence_transformers import CrossEncoder


model = CrossEncoder(
    "cross-encoder/ms-marco-MiniLM-L6-v2"
)

query = "remote employee network access"

candidates = [
    {
        "id": "remote-work",
        "text": (
            "Employees may work remotely up to two days per week. "
            "Remote employees must connect to internal company systems "
            "through the corporate VPN."
        )
    },
    {
        "id": "technical-support",
        "text": (
            "For general VPN connection problems, employees should verify "
            "their network connection and confirm that the corporate VPN "
            "client is running."
        )
    },
    {
        "id": "engineering-policy",
        "text": (
            "Production database access is restricted to authorized "
            "engineering employees."
        )
    }
]

pairs = [
    [query, candidate["text"]]
    for candidate in candidates
]

scores = model.predict(pairs)

for candidate, score in zip(candidates, scores):
    candidate["reranker_score"] = float(score)

ranked_candidates = sorted(
    candidates,
    key=lambda candidate: candidate["reranker_score"],
    reverse=True
)

for rank, candidate in enumerate(ranked_candidates, start=1):
    print(
        f"\nRank: {rank}\n"
        f"ID: {candidate['id']}\n"
        f"Reranker Score: {candidate['reranker_score']:.4f}\n"
        f"Text: {candidate['text']}"
    )