
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class QueryUsage:
    """
    Aggregate API usage for a single RAG query.

    Each generation attempt and embedding API call
    contributes to the query-level totals.
    """

    generation_attempts: int = 0

    generation_input_tokens: int = 0
    generation_cached_input_tokens: int = 0
    generation_output_tokens: int = 0

    embedding_api_calls: int = 0
    embedding_cache_hits: int = 0
    embedding_input_tokens: int = 0

    generation_cost_usd: Decimal = field(
        default_factory=lambda: Decimal("0")
    )
    embedding_cost_usd: Decimal = field(
        default_factory=lambda: Decimal("0")
    )

    @property
    def total_cost_usd(self) -> Decimal:
        return (
            self.generation_cost_usd
            + self.embedding_cost_usd
        )

    @property
    def total_api_tokens(self) -> int:
        return (
            self.generation_input_tokens
            + self.generation_output_tokens
            + self.embedding_input_tokens
        )

    def record_generation(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        cost_usd: Decimal,
    ) -> None:
        """
        Record one completed generation API attempt.

        Failed citation validation still counts because
        the LLM API call has already been made.
        """
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

        if cost_usd < 0:
            raise ValueError("Cost cannot be negative")

        self.generation_attempts += 1

        self.generation_input_tokens += input_tokens
        self.generation_cached_input_tokens += (
            cached_input_tokens
        )
        self.generation_output_tokens += output_tokens

        self.generation_cost_usd += cost_usd

    def record_embedding(
        self,
        input_tokens: int,
        cost_usd: Decimal,
    ) -> None:
        """
        Record one completed embedding API call.

        For batch embeddings, input_tokens represents
        the total tokens reported for the batch.
        """
        if input_tokens < 0:
            raise ValueError("Token counts cannot be negative")

        if cost_usd < 0:
            raise ValueError("Cost cannot be negative")

        self.embedding_api_calls += 1
        self.embedding_input_tokens += input_tokens
        self.embedding_cost_usd += cost_usd

    def record_embedding_cache_hit(self) -> None:
        """
        Record an embedding cache HIT.

        No new OpenAI embedding API usage is incurred.
        """
        self.embedding_cache_hits += 1

    def to_dict(self) -> dict:
        """
        Return a serializable usage summary.

        Decimal values are converted to strings
        to preserve precision.
        """
        return {
            "generation_attempts": self.generation_attempts,
            "generation_input_tokens": (
                self.generation_input_tokens
            ),
            "generation_cached_input_tokens": (
                self.generation_cached_input_tokens
            ),
            "generation_output_tokens": (
                self.generation_output_tokens
            ),
            "embedding_api_calls": self.embedding_api_calls,
            "embedding_cache_hits": self.embedding_cache_hits,
            "embedding_input_tokens": (
                self.embedding_input_tokens
            ),
            "total_api_tokens": self.total_api_tokens,
            "generation_cost_usd": str(
                self.generation_cost_usd
            ),
            "embedding_cost_usd": str(
                self.embedding_cost_usd
            ),
            "total_cost_usd": str(self.total_cost_usd),
        }


_current_query_usage: ContextVar[QueryUsage | None] = ContextVar(
    "current_rag_query_usage",
    default=None,
)


def start_query_usage() -> Token:
    """
    Start usage tracking for the current async context.

    Returns a token used to restore the previous context.
    """
    return _current_query_usage.set(QueryUsage())


def get_query_usage() -> QueryUsage | None:
    """
    Return the active query's usage collector.

    Returns None outside a tracked RAG query.
    """
    return _current_query_usage.get()


def reset_query_usage(token: Token) -> None:
    """
    Restore the previous context.

    Must be called in a finally block.
    """
    _current_query_usage.reset(token)


def record_generation_usage(
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    cost_usd: Decimal,
) -> None:
    """
    Record generation usage if a collector is active.
    """
    usage = get_query_usage()

    if usage is not None:
        usage.record_generation(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost_usd,
        )


def record_embedding_usage(
    input_tokens: int,
    cost_usd: Decimal,
) -> None:
    """
    Record embedding usage if a collector is active.
    """
    usage = get_query_usage()

    if usage is not None:
        usage.record_embedding(
            input_tokens=input_tokens,
            cost_usd=cost_usd,
        )


def record_embedding_cache_hit() -> None:
    """
    Record a cache HIT if a collector is active.
    """
    usage = get_query_usage()

    if usage is not None:
        usage.record_embedding_cache_hit()
