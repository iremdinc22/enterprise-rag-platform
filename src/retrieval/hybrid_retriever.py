from src.ingestion.embedder import create_embedding
from src.observability.tracing import get_tracer
from src.retrieval.bm25_retriever import search_bm25
from src.vector_store.qdrant_store import search_chunks


def reciprocal_rank_fusion(
    bm25_results,
    vector_results,
    k=60
):
    fused_results = {}

    # Add BM25 contributions
    for rank, result in enumerate(
        bm25_results,
        start=1
    ):
        point_id = result["point_id"]

        if point_id not in fused_results:
            fused_results[point_id] = {
                "point_id": point_id,
                "document_id": result["document_id"],
                "tenant_id": result["tenant_id"],
                "chunk_id": result["chunk_id"],
                "filename": result["filename"],
                "page": result["page"],
                "text": result["text"],
                "bm25_rank": None,
                "vector_rank": None,
                "bm25_score": None,
                "vector_score": None,
                "rrf_score": 0.0
            }

        fused_results[point_id]["bm25_rank"] = rank
        fused_results[point_id]["bm25_score"] = (
            result["score"]
        )
        fused_results[point_id]["rrf_score"] += (
            1 / (k + rank)
        )

    # Add vector contributions
    for rank, result in enumerate(
        vector_results,
        start=1
    ):
        point_id = str(result.id)
        payload = result.payload

        if point_id not in fused_results:
            fused_results[point_id] = {
                "point_id": point_id,
                "document_id": payload["document_id"],
                "tenant_id": payload["tenant_id"],
                "chunk_id": payload["chunk_id"],
                "filename": payload["filename"],
                "page": payload["page"],
                "text": payload["text"],
                "bm25_rank": None,
                "vector_rank": None,
                "bm25_score": None,
                "vector_score": None,
                "rrf_score": 0.0
            }

        fused_results[point_id]["vector_rank"] = rank
        fused_results[point_id]["vector_score"] = float(
            result.score
        )
        fused_results[point_id]["rrf_score"] += (
            1 / (k + rank)
        )

    ranked_results = sorted(
        fused_results.values(),
        key=lambda result: result["rrf_score"],
        reverse=True
    )

    return ranked_results


async def hybrid_search(
    query,
    tenant_id,
    retrieval_limit=5,
    final_limit=3,
    rrf_k=60
):
    tracer = get_tracer()

    # BM25 lexical retrieval
    with tracer.start_as_current_span(
        "retrieval.bm25"
    ) as span:
        bm25_results = await search_bm25(
            query=query,
            tenant_id=tenant_id,
            limit=retrieval_limit
        )

        span.set_attribute(
            "retrieval.bm25_result_count",
            len(bm25_results)
        )

    # Create query embedding
    with tracer.start_as_current_span(
        "retrieval.query_embedding"
    ) as span:
        query_embedding = await create_embedding(
            query
        )

        span.set_attribute(
            "retrieval.embedding_dimensions",
            len(query_embedding)
        )

    # Qdrant vector retrieval
    with tracer.start_as_current_span(
        "retrieval.vector"
    ) as span:
        vector_results = await search_chunks(
            query_vector=query_embedding,
            tenant_id=tenant_id,
            limit=retrieval_limit
        )

        span.set_attribute(
            "retrieval.vector_result_count",
            len(vector_results)
        )

    # Reciprocal Rank Fusion
    with tracer.start_as_current_span(
        "retrieval.rrf"
    ) as span:
        fused_results = reciprocal_rank_fusion(
            bm25_results=bm25_results,
            vector_results=vector_results,
            k=rrf_k
        )

        span.set_attribute(
            "retrieval.rrf_k",
            rrf_k
        )
        span.set_attribute(
            "retrieval.fused_candidate_count",
            len(fused_results)
        )

        final_results = fused_results[:final_limit]

        span.set_attribute(
            "retrieval.returned_candidate_count",
            len(final_results)
        )

    return final_results
