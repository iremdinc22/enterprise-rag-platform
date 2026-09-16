import asyncio
import numpy as np

from src.ingestion.embedder import create_embeddings
from src.retrieval.reranker import rerank


candidates = [
    {
        "id": "chunk-1",
        "text": "Remote employees must connect to company systems using the corporate VPN."
    },
    {
        "id": "chunk-2",
        "text": "Employees working remotely are required to use the approved corporate VPN."
    },
    {
        "id": "chunk-3",
        "text": "The corporate VPN must be used when accessing internal systems from outside the office."
    },
    {
        "id": "chunk-4",
        "text": "Employees may work remotely up to two days per week."
    },
    {
        "id": "chunk-5",
        "text": "Employees should avoid public or unsecured networks while working remotely."
    }
]


def cosine_similarity(vector_a, vector_b):
    vector_a = np.array(vector_a)
    vector_b = np.array(vector_b)

    return np.dot(vector_a, vector_b) / (
        np.linalg.norm(vector_a)
        * np.linalg.norm(vector_b)
    )


def normalize_scores(candidates):
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


async def main():
    query = "How do employees securely access company systems remotely?"

    lambda_value = 0.5
    final_limit = 3

    # Calculate real relevance scores using the reranker
    reranked_candidates = rerank(
        query=query,
        candidates=candidates,
        limit=len(candidates)
    )

    # Normalize reranker scores to the 0-1 range
    normalized_candidates = normalize_scores(
        reranked_candidates
    )

    print("\n=== REAL RERANKER SCORES ===")

    for candidate in normalized_candidates:
        print(
            f"{candidate['id']} | "
            f"reranker={candidate['reranker_score']:.4f} | "
            f"normalized={candidate['relevance']:.4f}"
        )

    # Create embeddings for candidate-to-candidate similarity
    texts = [
        candidate["text"]
        for candidate in normalized_candidates
    ]

    embeddings = await create_embeddings(texts)

    for candidate, embedding in zip(
        normalized_candidates,
        embeddings
    ):
        candidate["embedding"] = embedding

    # Start with the most relevant candidate
    selected = [normalized_candidates[0]]
    remaining = normalized_candidates[1:]

    print("\n=== FIRST SELECTION ===")
    print(
        f"Selected: {selected[0]['id']} "
        f"(relevance={selected[0]['relevance']:.4f})"
    )

    # Select the remaining candidates using MMR
    while remaining and len(selected) < final_limit:
        best_candidate = None
        best_mmr_score = float("-inf")

        print("\n=== MMR ROUND ===")

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

            print(
                f"{candidate['id']} | "
                f"relevance={candidate['relevance']:.4f} | "
                f"max_similarity={max_similarity:.4f} | "
                f"mmr={mmr_score:.4f}"
            )

            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_candidate = candidate

        selected.append(best_candidate)
        remaining.remove(best_candidate)

        print(
            f"Selected: {best_candidate['id']} "
            f"(MMR={best_mmr_score:.4f})"
        )

    print("\n=== FINAL MMR SELECTION ===")

    for rank, candidate in enumerate(selected, start=1):
        print(
            f"\nRank: {rank}\n"
            f"ID: {candidate['id']}\n"
            f"Reranker Score: {candidate['reranker_score']:.4f}\n"
            f"Normalized Relevance: {candidate['relevance']:.4f}\n"
            f"Text: {candidate['text']}"
        )


asyncio.run(main())