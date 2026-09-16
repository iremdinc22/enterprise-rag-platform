from sentence_transformers import CrossEncoder


MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2"

model = CrossEncoder(MODEL_NAME)


def rerank(query, candidates, limit=3):
    if not query or not query.strip():
        raise ValueError("query cannot be empty")

    if limit <= 0:
        raise ValueError("limit must be greater than 0")

    if not candidates:
        return []

    pairs = [
        [query, candidate["text"]]
        for candidate in candidates
    ]

    scores = model.predict(pairs)

    reranked_candidates = []

    for candidate, score in zip(candidates, scores):
        reranked_candidates.append({
            **candidate,
            "reranker_score": float(score)
        })

    reranked_candidates.sort(
        key=lambda candidate: candidate["reranker_score"],
        reverse=True
    )

    return reranked_candidates[:limit]