from sentence_transformers import CrossEncoder
from threading import Lock


from src.config import RERANKER_MODEL, RERANKER_REVISION

MODEL_NAME = RERANKER_MODEL

model = CrossEncoder(MODEL_NAME, revision=RERANKER_REVISION)
_model_lock = Lock()


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

    with _model_lock:
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
