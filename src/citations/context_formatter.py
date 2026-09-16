def format_citation_context(citations):
    if not citations:
        return ""

    formatted_sources = []

    for citation in citations:
        provenance_lines = []

        for source in citation["sources"]:
            provenance_lines.append(
                f"- File: {source['filename']} | "
                f"Page: {source['page']} | "
                f"Chunk: {source['chunk_id']}"
            )

        provenance = "\n".join(provenance_lines)

        formatted_source = (
            f"[SOURCE {citation['citation_id']}]\n"
            f"Provenance:\n"
            f"{provenance}\n"
            f"Content:\n"
            f"{citation['text']}"
        )

        formatted_sources.append(formatted_source)

    return "\n\n".join(formatted_sources)