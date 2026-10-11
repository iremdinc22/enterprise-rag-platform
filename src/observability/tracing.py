import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)


SERVICE_NAME = "enterprise-rag-platform"


def configure_tracing() -> bool:
    """
    Enable OpenTelemetry console tracing when explicitly requested.

    Tracing remains disabled by default so normal tests and
    evaluation runs do not produce additional trace output.
    """
    enabled = os.getenv("RAG_TRACING_ENABLED", "false").lower() == "true"

    if not enabled:
        return False

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": SERVICE_NAME}
        )
    )

    provider.add_span_processor(
        SimpleSpanProcessor(ConsoleSpanExporter())
    )

    trace.set_tracer_provider(provider)
    return True


def get_tracer():
    return trace.get_tracer(SERVICE_NAME)
