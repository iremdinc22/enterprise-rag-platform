
def precision_at_k(retrieved_ids, relevant_ids, k):
    if k <= 0:
        raise ValueError("k must be greater than 0")

    relevant = set(relevant_ids)
    top_k = retrieved_ids[:k]

    relevant_count = sum(
        1 for chunk_id in top_k
        if chunk_id in relevant
    )

    return relevant_count / k


def recall_at_k(retrieved_ids, relevant_ids, k):
    if k <= 0:
        raise ValueError("k must be greater than 0")

    relevant = set(relevant_ids)

    if not relevant:
        return None

    top_k = set(retrieved_ids[:k])
    found_relevant = relevant.intersection(top_k)

    return len(found_relevant) / len(relevant)


def reciprocal_rank(retrieved_ids, relevant_ids):
    relevant = set(relevant_ids)

    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in relevant:
            return 1.0 / rank

    return 0.0


def mean_reciprocal_rank(evaluation_results):
    if not evaluation_results:
        return 0.0

    scores = [
        reciprocal_rank(
            result["retrieved_ids"],
            result["relevant_ids"]
        )
        for result in evaluation_results
    ]

    return sum(scores) / len(scores)
