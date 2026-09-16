import numpy as np


def cosine_similarity(vector_a, vector_b):
    vector_a = np.array(vector_a)
    vector_b = np.array(vector_b)

    return np.dot(vector_a, vector_b) / (
        np.linalg.norm(vector_a)
        * np.linalg.norm(vector_b)
    )


def normalize_relevance_scores(candidates):
    if not candidates:
        return []

    scores = [
        candidate["reranker_score"]
        for candidate in candidates
    ]

    min_score = min(scores)
    max_score = max(scores)

    if max_score == min_score:
        for candidate in candidates:
            candidate["relevance"] = 1.0

        return candidates

    for candidate in candidates:
        candidate["relevance"] = (
            candidate["reranker_score"] - min_score
        ) / (
            max_score - min_score
        )

    return candidates


def select_with_mmr(
    candidates,
    limit=3,
    lambda_value=0.5
):
    if not candidates:
        return []

    if limit <= 0:
        raise ValueError("limit must be greater than 0")

    if not 0 <= lambda_value <= 1:
        raise ValueError(
            "lambda_value must be between 0 and 1"
        )

    normalized_candidates = normalize_relevance_scores(
        candidates
    )

    # Start with the most relevant candidate
    first_candidate = max(
        normalized_candidates,
        key=lambda candidate: candidate["relevance"]
    )

    selected = [first_candidate]

    remaining = [
        candidate
        for candidate in normalized_candidates
        if candidate is not first_candidate
    ]

    while remaining and len(selected) < limit:
        best_candidate = None
        best_mmr_score = float("-inf")

        for candidate in remaining:
            similarities = [
                cosine_similarity(
                    candidate["embedding"],
                    selected_candidate["embedding"]
                )
                for selected_candidate in selected
            ]

            max_similarity = max(similarities)

            mmr_score = (
                lambda_value * candidate["relevance"]
                - (1 - lambda_value) * max_similarity
            )

            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_candidate = candidate

        selected.append({
            **best_candidate,
            "mmr_score": best_mmr_score
        })

        remaining.remove(best_candidate)

    return selected