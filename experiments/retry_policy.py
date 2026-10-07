import httpx
import openai

from src.workers.celery_app import is_retryable_error


def create_response(status_code):
    request = httpx.Request(
        "POST",
        "https://api.openai.com/v1/embeddings"
    )

    return httpx.Response(
        status_code=status_code,
        request=request
    )


def main():
    rate_limit_error = openai.RateLimitError(
        "Rate limit exceeded",
        response=create_response(429),
        body=None
    )

    service_unavailable_error = openai.InternalServerError(
        "Service unavailable",
        response=create_response(503),
        body=None
    )

    authentication_error = openai.AuthenticationError(
        "Invalid API key",
        response=create_response(401),
        body=None
    )

    errors = [
        (
            "HTTPX connection error",
            httpx.ConnectError("Connection failed")
        ),
        (
            "HTTPX timeout",
            httpx.ReadTimeout("Request timed out")
        ),
        (
            "OpenAI 429",
            rate_limit_error
        ),
        (
            "OpenAI 503",
            service_unavailable_error
        ),
        (
            "OpenAI 401",
            authentication_error
        ),
        (
            "File not found",
            FileNotFoundError("Document does not exist")
        ),
        (
            "Programming error",
            TypeError("Unexpected type")
        ),
    ]

    for name, error in errors:
        retryable = is_retryable_error(error)

        print(
            f"{name:<25} -> "
            f"{'RETRY' if retryable else 'FAIL'}"
        )


if __name__ == "__main__":
    main()