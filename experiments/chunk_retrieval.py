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
# 2. Raw document
# -------------------------

text = """
Employees may work remotely up to two days per week and are expected to maintain normal working hours while away from the office. Remote employees must connect to internal company systems through the approved VPN service and should avoid using public or unsecured networks. Access to sensitive internal applications may require multi-factor authentication, and employees are responsible for keeping company devices updated with the latest security patches. Managers may revoke remote access if security policies are repeatedly violated.

Employees receive 20 days of annual leave. Unused leave may be carried over to the following year subject to company policy.

Expense reports must be submitted within 30 days. Reports submitted after this period may require additional approval.

The engineering team primarily uses PostgreSQL for relational data. Production database access is restricted to authorized employees.
"""


# -------------------------
# 3. Chunking
# -------------------------

def chunk_text(text, chunk_size=40, chunk_overlap=8):
    paragraphs = [
        paragraph.strip()
        for paragraph in text.split("\n\n")
        if paragraph.strip()
    ]

    chunks = []

    for paragraph in paragraphs:
        words = paragraph.split()

        if len(words) <= chunk_size:
            chunks.append(paragraph)
            continue

        start = 0

        while start < len(words):
            end = start + chunk_size

            chunk_words = words[start:end]
            chunk = " ".join(chunk_words)

            chunks.append(chunk)

            start = end - chunk_overlap

    return chunks


chunks = chunk_text(
    text,
    chunk_size=40,
    chunk_overlap=8
)


# -------------------------
# 4. Create chunk embeddings
# -------------------------

chunk_response = client.embeddings.create(
    model="text-embedding-3-small",
    input=chunks,
)

chunk_embeddings = [
    item.embedding
    for item in chunk_response.data
]


# -------------------------
# 5. Query
# -------------------------

query = "How should employees securely connect when working remotely?"

query_response = client.embeddings.create(
    model="text-embedding-3-small",
    input=query,
)

query_embedding = query_response.data[0].embedding


# -------------------------
# 6. Cosine similarity
# -------------------------

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)

    return np.dot(a, b) / (
        np.linalg.norm(a) * np.linalg.norm(b)
    )


# -------------------------
# 7. Calculate scores
# -------------------------

results = []

for i, chunk_embedding in enumerate(chunk_embeddings):
    score = cosine_similarity(
        query_embedding,
        chunk_embedding
    )

    results.append({
        "chunk": chunks[i],
        "score": score
    })


# -------------------------
# 8. Rank
# -------------------------

results.sort(
    key=lambda item: item["score"],
    reverse=True
)


# -------------------------
# 9. Top-K
# -------------------------

top_k = 3

top_results = results[:top_k]


# -------------------------
# 10. Print
# -------------------------

print(f"\nQuery: {query}")
print(f"\nTotal chunks: {len(chunks)}")
print(f"\nTop-{top_k} results:\n")

for rank, result in enumerate(top_results, start=1):
    print(f"{rank}. Score: {result['score']:.4f}")
    print(result["chunk"])
    print()

    