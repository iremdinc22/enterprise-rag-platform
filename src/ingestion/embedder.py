import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.cache.embedding_cache import (
    cache_embedding,
    get_cached_embedding
)


load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


async def create_embedding(text, model="text-embedding-3-small"):
    if not text or not text.strip():
        raise ValueError("text cannot be empty")

    cached_embedding = get_cached_embedding(
        text=text,
        model=model
    )

    if cached_embedding is not None:
        print("Embedding cache: HIT")
        return cached_embedding

    print("Embedding cache: MISS")

    response = await client.embeddings.create(
        model=model,
        input=text
    )

    embedding = response.data[0].embedding

    cache_embedding(
        text=text,
        model=model,
        embedding=embedding
    )

    return embedding


async def create_embeddings(
    texts,
    model="text-embedding-3-small"
):
    if not texts:
        return []

    if any(not text or not text.strip() for text in texts):
        raise ValueError("texts cannot contain empty values")

    response = await client.embeddings.create(
        model=model,
        input=texts
    )

    return [
        item.embedding
        for item in response.data
    ]