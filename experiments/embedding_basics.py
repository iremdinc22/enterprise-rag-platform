import os

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI


# -------------------------
# 1. Setup
# -------------------------

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


# -------------------------
# 2. Example documents
# -------------------------

documents = [
    "Employees receive 20 days of annual leave.",
    "Remote work is allowed two days per week.",
    "Employees must connect through VPN when working remotely.",
    "Expense reports must be submitted within 30 days.",
    "The engineering team uses PostgreSQL."
]


# -------------------------
# 3. Create document embeddings
# -------------------------

document_response = client.embeddings.create(
    model="text-embedding-3-small",
    input=documents
)

document_embeddings = [
    item.embedding
    for item in document_response.data
]


print("Embedding type:", type(document_embeddings[0]))
print("Embedding dimensions:", len(document_embeddings[0]))
print("Number of documents:", len(document_embeddings))


# -------------------------
# 4. Query
# -------------------------

query = "How many vacation days do employees receive?"

query_response = client.embeddings.create(
    model="text-embedding-3-small",
    input=query
)

query_embedding = query_response.data[0].embedding


# -------------------------
# 5. Cosine similarity
# -------------------------

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)

    dot_product = np.dot(a, b)

    magnitude_a = np.linalg.norm(a)
    magnitude_b = np.linalg.norm(b)

    return dot_product / (
        magnitude_a * magnitude_b
    )


# -------------------------
# 6. Calculate similarity scores
# -------------------------

results = []

for i, document_embedding in enumerate(document_embeddings):
    score = cosine_similarity(
        query_embedding,
        document_embedding
    )

    results.append({
        "document": documents[i],
        "score": score
    })


# -------------------------
# 7. Rank results
# -------------------------

results.sort(
    key=lambda item: item["score"],
    reverse=True
)


# -------------------------
# 8. Print all results
# -------------------------

print(f"\nQuery: {query}")
print("\nAll results:\n")

for rank, result in enumerate(results, start=1):
    print(
        f"{rank}. "
        f"Score: {result['score']:.4f} | "
        f"{result['document']}"
    )


# -------------------------
# 9. Top-K
# -------------------------

top_k = 2

top_results = results[:top_k]


print(f"\nTop-{top_k} results:\n")

for rank, result in enumerate(top_results, start=1):
    print(
        f"{rank}. "
        f"Score: {result['score']:.4f} | "
        f"{result['document']}"
    )