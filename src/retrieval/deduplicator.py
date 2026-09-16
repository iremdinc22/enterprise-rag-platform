import re


def normalize_text(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    return re.sub(
        r"\s+",
        " ",
        text.strip().lower()
    )


def deduplicate_exact(candidates):
    if not candidates:
        return []

    seen_texts = set()
    unique_candidates = []

    for candidate in candidates:
        normalized_text = normalize_text(
            candidate["text"]
        )

        if normalized_text in seen_texts:
            continue

        seen_texts.add(normalized_text)
        unique_candidates.append(candidate)

    return unique_candidates