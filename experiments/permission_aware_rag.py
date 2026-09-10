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
# 2. Structured document sections
# -------------------------

sections = [
    {
        "section": "remote-work",
        "page": 5,
        "allowed_roles": ["employee", "manager", "engineer"],
        "text": """
Employees may work remotely up to two days per week and are expected to maintain normal working hours while away from the office. Remote employees must connect to internal company systems through the approved VPN service and should avoid using public or unsecured networks. Access to sensitive internal applications may require multi-factor authentication, and employees are responsible for keeping company devices updated with the latest security patches. Managers may revoke remote access if security policies are repeatedly violated.
"""
    },
    {
        "section": "leave-policy",
        "page": 3,
        "allowed_roles": ["employee", "manager", "engineer"],
        "text": """
Employees receive 20 days of annual leave. Unused leave may be carried over to the following year subject to company policy.
"""
    },
    {
        "section": "expenses",
        "page": 7,
        "allowed_roles": ["employee", "manager", "finance", "engineer"],
        "text": """
Expense reports must be submitted within 30 days. Reports submitted after this period may require additional approval.
"""
    },
    {
        "section": "database",
        "page": 12,
        "allowed_roles": ["engineer"],
        "text": """
The engineering team primarily uses PostgreSQL for relational data. Production database access is restricted to authorized employees.
"""
    }
]


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


# -------------------------
# 4. Create chunk records
#    + propagate metadata
# -------------------------

chunk_records = []

chunk_number = 1

for section in sections:
    section_chunks = chunk_text(
        section["text"],
        chunk_size=40,
        chunk_overlap=8
    )

    for chunk in section_chunks:
        chunk_records.append({
            "chunk_id": f"chunk-{chunk_number:03d}",
            "document_id": "employee-handbook",
            "section": section["section"],
            "page": section["page"],
            "allowed_roles": section["allowed_roles"],
            "text": chunk
        })

        chunk_number += 1


# -------------------------
# 5. Create chunk embeddings
# -------------------------

chunk_response = client.embeddings.create(
    model="text-embedding-3-small",
    input=[
        record["text"]
        for record in chunk_records
    ],
)

chunk_embeddings = [
    item.embedding
    for item in chunk_response.data
]


# -------------------------
# 6. User and query
# -------------------------

user_role = "engineer"

query = "Which database does the engineering team use?"

query_response = client.embeddings.create(
    model="text-embedding-3-small",
    input=query,
)

query_embedding = query_response.data[0].embedding


# -------------------------
# 7. Cosine similarity
# -------------------------

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)

    return np.dot(a, b) / (
        np.linalg.norm(a) * np.linalg.norm(b)
    )


# -------------------------
# 8. Permission-aware scoring
# -------------------------

results = []

for i, chunk_embedding in enumerate(chunk_embeddings):
    record = chunk_records[i]

    # User cannot retrieve unauthorized chunks
    if user_role not in record["allowed_roles"]:
        continue

    score = cosine_similarity(
        query_embedding,
        chunk_embedding
    )

    results.append({
        "chunk_id": record["chunk_id"],
        "document_id": record["document_id"],
        "section": record["section"],
        "page": record["page"],
        "allowed_roles": record["allowed_roles"],
        "text": record["text"],
        "score": score
    })


# -------------------------
# 9. Rank results
# -------------------------

results.sort(
    key=lambda item: item["score"],
    reverse=True
)


# -------------------------
# 10. Threshold + Top-K
# -------------------------

similarity_threshold = 0.40
top_k = 3

top_results = [
    result
    for result in results
    if result["score"] >= similarity_threshold
][:top_k]


# -------------------------
# 11. Print retrieval results
# -------------------------

print(f"\nUser role: {user_role}")
print(f"Query: {query}")
print(f"\nTotal chunks: {len(chunk_records)}")
print(f"Accessible chunks: {len(results)}")
print(f"Similarity threshold: {similarity_threshold}")
print(f"\nTop-{top_k} accessible results:\n")

if not top_results:
    print("No sufficiently relevant accessible context found.")
    print("\nGenerated Answer:")
    print("I don't know based on the provided context.")

else:
    for rank, result in enumerate(top_results, start=1):
        print(f"{rank}. Score: {result['score']:.4f}")
        print(f"Chunk ID: {result['chunk_id']}")
        print(f"Document: {result['document_id']}")
        print(f"Section: {result['section']}")
        print(f"Page: {result['page']}")
        print(f"Allowed roles: {result['allowed_roles']}")
        print(result["text"])
        print()


    # -------------------------
    # 12. Build context
    #     + provenance metadata
    # -------------------------

    context_parts = []

    for result in top_results:
        context_part = f"""
[Source]
Chunk ID: {result['chunk_id']}
Document: {result['document_id']}
Section: {result['section']}
Page: {result['page']}

[Content]
{result['text']}
"""

        context_parts.append(context_part)

    context = "\n".join(context_parts)


    # -------------------------
    # 13. Grounded prompt
    #     + citation instructions
    # -------------------------

    prompt = f"""
Answer the user's question using only the provided context.

Do not use outside knowledge.

For factual claims, cite the supporting source using this format:

[Document: <document_id>, Section: <section>, Page: <page>]

Use only source metadata that appears in the provided context.
Do not invent document names, sections, or page numbers.

If the answer cannot be found in the provided context, say:
"I don't know based on the provided context."

Context:
{context}

Question:
{query}
"""


    # -------------------------
    # 14. Generate answer
    # -------------------------

    response = client.responses.create(
        model="gpt-5.6",
        input=prompt
    )

    answer = response.output_text


    # -------------------------
    # 15. Print final answer
    # -------------------------

    print("\nGenerated Answer:\n")
    print(answer)