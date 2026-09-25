import re

from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue
)
from rank_bm25 import BM25Okapi

from src.vector_store.qdrant_store import (
    client,
    COLLECTION_NAME
)


def tokenize(text):
    return re.findall(
        r"\b[\w-]+\b",
        text.lower()
    )


async def load_chunks(tenant_id):
    points, _ = await client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(
                        value=tenant_id
                    )
                )
            ]
        ),
        limit=100,
        with_payload=True
    )

    chunks = []

    for point in points:
        chunks.append({
            "point_id": str(point.id),
            "document_id": point.payload["document_id"],
            "tenant_id": point.payload["tenant_id"],
            "chunk_id": point.payload["chunk_id"],
            "filename": point.payload["filename"],
            "page": point.payload["page"],
            "text": point.payload["text"]
        })

    return chunks


async def build_bm25_index(tenant_id):
    chunks = await load_chunks(
        tenant_id=tenant_id
    )

    tokenized_corpus = [
        tokenize(chunk["text"])
        for chunk in chunks
    ]

    bm25 = BM25Okapi(
        tokenized_corpus
    )

    return bm25, chunks


async def search_bm25(
    query,
    tenant_id,
    limit=3
):
    bm25, chunks = await build_bm25_index(
        tenant_id=tenant_id
    )

    query_tokens = tokenize(query)

    scores = bm25.get_scores(
        query_tokens
    )

    ranked_indices = sorted(
        range(len(scores)),
        key=lambda index: scores[index],
        reverse=True
    )

    results = []

    for index in ranked_indices:
        score = float(scores[index])

        if score <= 0:
            continue

        chunk = chunks[index]

        results.append({
            **chunk,
            "score": score
        })

        if len(results) >= limit:
            break

    return results