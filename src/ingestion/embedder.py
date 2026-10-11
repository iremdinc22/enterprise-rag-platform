
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.cache.embedding_cache import (
    cache_embedding,
    get_cached_embedding,
)
from src.observability.tracing import get_tracer
from src.observability.costs import estimate_embedding_cost
from src.observability.usage_tracker import (
    record_embedding_usage,
    record_embedding_cache_hit,
)


load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    timeout=20.0,
    max_retries=1,
)


async def create_embedding(
    text,
    model="text-embedding-3-small",
):
    if not text or not text.strip():
        raise ValueError("text cannot be empty")

    tracer = get_tracer()

    with tracer.start_as_current_span(
        "embedding.single"
    ) as span:
        span.set_attribute("embedding.model", model)
        span.set_attribute("embedding.input_count", 1)

        # Check Redis embedding cache
        cached_embedding = get_cached_embedding(
            text=text,
            model=model,
        )

        if cached_embedding is not None:
            print("Embedding cache: HIT")

            span.set_attribute("embedding.cache_hit", True)
            span.set_attribute("embedding.api_called", False)
            span.set_attribute("embedding.input_tokens", 0)
            span.set_attribute("embedding.total_tokens", 0)
            span.set_attribute(
                "embedding.estimated_cost_usd",
                0.0,
            )

            # Track cache HIT at query level.
            # No new embedding API cost is incurred.
            record_embedding_cache_hit()

            return cached_embedding

        print("Embedding cache: MISS")

        span.set_attribute("embedding.cache_hit", False)

        # Request embedding from OpenAI
        response = await client.embeddings.create(
            model=model,
            input=text,
        )

        span.set_attribute("embedding.api_called", True)

        # Track actual API token usage and estimated cost
        if response.usage is not None:
            input_tokens = response.usage.prompt_tokens
            total_tokens = response.usage.total_tokens

            estimated_cost = estimate_embedding_cost(
                model=model,
                input_tokens=input_tokens,
            )

            # Aggregate embedding API usage
            # in the active query-level collector.
            record_embedding_usage(
                input_tokens=input_tokens,
                cost_usd=estimated_cost,
            )

            span.set_attribute(
                "embedding.input_tokens",
                input_tokens,
            )
            span.set_attribute(
                "embedding.total_tokens",
                total_tokens,
            )
            span.set_attribute(
                "embedding.estimated_cost_usd",
                float(estimated_cost),
            )

        embedding = response.data[0].embedding

        # Save embedding to Redis cache
        cache_embedding(
            text=text,
            model=model,
            embedding=embedding,
        )

        return embedding


async def create_embeddings(
    texts,
    model="text-embedding-3-small",
):
    if not texts:
        return []

    if any(not text or not text.strip() for text in texts):
        raise ValueError(
            "texts cannot contain empty values"
        )

    tracer = get_tracer()

    with tracer.start_as_current_span(
        "embedding.batch"
    ) as span:
        span.set_attribute("embedding.model", model)
        span.set_attribute(
            "embedding.input_count",
            len(texts),
        )

        # Batch embeddings currently bypass Redis cache.
        span.set_attribute("embedding.cache_hit", False)

        # Request embeddings for the entire batch
        response = await client.embeddings.create(
            model=model,
            input=texts,
        )

        span.set_attribute("embedding.api_called", True)

        # Track batch-level API token usage and cost
        if response.usage is not None:
            input_tokens = response.usage.prompt_tokens
            total_tokens = response.usage.total_tokens

            estimated_cost = estimate_embedding_cost(
                model=model,
                input_tokens=input_tokens,
            )

            # Record once per API batch,
            # not once per individual text.
            record_embedding_usage(
                input_tokens=input_tokens,
                cost_usd=estimated_cost,
            )

            span.set_attribute(
                "embedding.input_tokens",
                input_tokens,
            )
            span.set_attribute(
                "embedding.total_tokens",
                total_tokens,
            )
            span.set_attribute(
                "embedding.estimated_cost_usd",
                float(estimated_cost),
            )

        return [
            item.embedding
            for item in response.data
        ]
