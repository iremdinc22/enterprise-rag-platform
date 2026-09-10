def chunk_text(text, chunk_size=40, chunk_overlap=8):
    words = text.split()

    if len(words) <= chunk_size:
        return [text.strip()]

    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size

        chunk_words = words[start:end]
        chunk = " ".join(chunk_words)

        chunks.append(chunk)

        start = end - chunk_overlap

    return chunks