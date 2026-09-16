def build_citations(contexts):
    if not contexts:
        return []

    citations = []

    for citation_id, context in enumerate(
        contexts,
        start=1
    ):
        sources = context.get("sources")

        if not sources:
            sources = [
                {
                    "document_id": context["document_id"],
                    "filename": context["filename"],
                    "page": context["page"],
                    "chunk_id": context["chunk_id"]
                }
            ]

        citation = {
            "citation_id": citation_id,
            "text": context["text"],
            "sources": sources
        }

        citations.append(citation)

    return citations


def build_citation_response(citations):
    if not citations:
        return []

    response = []

    for citation in citations:
        response.append({
            "citation_id": citation["citation_id"],
            "sources": citation["sources"]
        })

    return response