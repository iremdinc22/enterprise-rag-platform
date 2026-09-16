import asyncio
import numpy as np

from src.ingestion.embedder import create_embeddings


candidates = [
    "Remote employees must connect to company systems using the corporate VPN.",
    "Employees working remotely are required to use the approved corporate VPN.",
    "The corporate VPN must be used when accessing internal systems from outside the office.",
    "Employees may work remotely up to two days per week.",
    "Employees should avoid public or unsecured networks while working remotely."
]


def cosine_similarity(vector_a, vector_b):
    vector_a = np.array(vector_a)
    vector_b = np.array(vector_b)

    return np.dot(vector_a, vector_b) / (
        np.linalg.norm(vector_a)
        * np.linalg.norm(vector_b)
    )


async def main():
    embeddings = await create_embeddings(candidates)

    print("\n=== CANDIDATE SIMILARITY MATRIX ===\n")

    print(
        "          "
        + " ".join(
            f"chunk-{i + 1:>2}"
            for i in range(len(candidates))
        )
    )

    for i in range(len(candidates)):
        similarities = []

        for j in range(len(candidates)):
            similarity = cosine_similarity(
                embeddings[i],
                embeddings[j]
            )

            similarities.append(
                f"{similarity:.4f}"
            )

        print(
            f"chunk-{i + 1:<2}  "
            + "   ".join(similarities)
        )


asyncio.run(main())