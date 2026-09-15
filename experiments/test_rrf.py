from types import SimpleNamespace

from src.retrieval.hybrid_retriever import reciprocal_rank_fusion


def main():
    bm25_results = [
        {
            "point_id": "chunk-A",
            "document_id": "doc-1",
            "chunk_id": "chunk-001",
            "filename": "document.pdf",
            "page": 1,
            "text": "Text for chunk A",
            "score": 2.5
        },
        {
            "point_id": "chunk-B",
            "document_id": "doc-1",
            "chunk_id": "chunk-002",
            "filename": "document.pdf",
            "page": 1,
            "text": "Text for chunk B",
            "score": 1.8
        },
        {
            "point_id": "chunk-C",
            "document_id": "doc-1",
            "chunk_id": "chunk-003",
            "filename": "document.pdf",
            "page": 2,
            "text": "Text for chunk C",
            "score": 1.2
        }
    ]

    vector_results = [
        SimpleNamespace(
            id="chunk-B",
            score=0.82,
            payload={
                "document_id": "doc-1",
                "chunk_id": "chunk-002",
                "filename": "document.pdf",
                "page": 1,
                "text": "Text for chunk B"
            }
        ),
        SimpleNamespace(
            id="chunk-C",
            score=0.71,
            payload={
                "document_id": "doc-1",
                "chunk_id": "chunk-003",
                "filename": "document.pdf",
                "page": 2,
                "text": "Text for chunk C"
            }
        ),
        SimpleNamespace(
            id="chunk-D",
            score=0.65,
            payload={
                "document_id": "doc-2",
                "chunk_id": "chunk-001",
                "filename": "another-document.pdf",
                "page": 1,
                "text": "Text for chunk D"
            }
        )
    ]

    results = reciprocal_rank_fusion(
        bm25_results=bm25_results,
        vector_results=vector_results,
        k=0
    )

    for rank, result in enumerate(results, start=1):
        print(
            f"\nFinal Rank: {rank}\n"
            f"Point ID: {result['point_id']}\n"
            f"Document: {result['document_id']}\n"
            f"Chunk: {result['chunk_id']}\n"
            f"BM25 Rank: {result['bm25_rank']}\n"
            f"Vector Rank: {result['vector_rank']}\n"
            f"BM25 Score: {result['bm25_score']}\n"
            f"Vector Score: {result['vector_score']}\n"
            f"RRF Score: {result['rrf_score']:.4f}\n"
            f"Text: {result['text']}"
        )


main()