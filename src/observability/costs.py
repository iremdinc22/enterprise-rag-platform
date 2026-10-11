
from dataclasses import dataclass
from decimal import Decimal


TOKENS_PER_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class GenerationPricing:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal


GENERATION_PRICING = {
    "gpt-4.1-mini": GenerationPricing(
        input_per_million=Decimal("0.40"),
        cached_input_per_million=Decimal("0.10"),
        output_per_million=Decimal("1.60"),
    ),
}


EMBEDDING_PRICING = {
    "text-embedding-3-small": Decimal("0.02"),
}


def estimate_generation_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> Decimal:
    """
    Estimate generation API cost in USD.

    cached_input_tokens is a subset of input_tokens.
    """
    if model not in GENERATION_PRICING:
        raise ValueError(f"Unsupported generation model: {model}")

    if (
        input_tokens < 0
        or output_tokens < 0
        or cached_input_tokens < 0
    ):
        raise ValueError("Token counts cannot be negative")

    if cached_input_tokens > input_tokens:
        raise ValueError(
            "Cached input tokens cannot exceed input tokens"
        )

    pricing = GENERATION_PRICING[model]

    uncached_input_tokens = (
        input_tokens - cached_input_tokens
    )

    input_cost = (
        Decimal(uncached_input_tokens)
        * pricing.input_per_million
        / TOKENS_PER_MILLION
    )

    cached_input_cost = (
        Decimal(cached_input_tokens)
        * pricing.cached_input_per_million
        / TOKENS_PER_MILLION
    )

    output_cost = (
        Decimal(output_tokens)
        * pricing.output_per_million
        / TOKENS_PER_MILLION
    )

    return input_cost + cached_input_cost + output_cost


def estimate_embedding_cost(
    model: str,
    input_tokens: int,
) -> Decimal:
    """
    Estimate embedding API cost in USD.
    """
    if model not in EMBEDDING_PRICING:
        raise ValueError(f"Unsupported embedding model: {model}")

    if input_tokens < 0:
        raise ValueError("Token counts cannot be negative")

    return (
        Decimal(input_tokens)
        * EMBEDDING_PRICING[model]
        / TOKENS_PER_MILLION
    )
