import re


def normalize_text(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    return re.sub(
        r"\s+",
        " ",
        text.strip().lower()
    )


def build_source(candidate):
    return {
        "document_id": candidate["document_id"],
        "filename": candidate["filename"],
        "page": candidate["page"],
        "chunk_id": candidate["chunk_id"]
    }


def deduplicate_exact(candidates):
    if not candidates:
        return []

    unique_candidates = []
    candidates_by_text = {}

    for candidate in candidates:
        normalized_text = normalize_text(candidate["text"])
        source = build_source(candidate)

        if normalized_text in candidates_by_text:
            existing_candidate = candidates_by_text[
                normalized_text
            ]

            # Avoid adding the same source multiple times
            if source not in existing_candidate["sources"]:
                existing_candidate["sources"].append(source)

            # Skip creating another candidate for the same text
            continue

        # Create a new candidate for previously unseen text
        unique_candidate = {
            **candidate,
            "sources": [source]
        }

        candidates_by_text[normalized_text] = unique_candidate
        unique_candidates.append(unique_candidate)

    return unique_candidates