import os

from dotenv import load_dotenv
from openai import AsyncOpenAI


load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


async def create_embedding(
    text,
    model="text-embedding-3-small"
):
    if not text or not text.strip():
        raise ValueError("text cannot be empty")

    response = await client.embeddings.create(
        model=model,
        input=text
    )

    return response.data[0].embedding


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
