

import argparse
import asyncio
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from src.auth.models import Role, UserContext
from src.rag.pipeline import run_rag


load_dotenv(dotenv_path=".env")

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_TENANT = "acme"

RESULTS_DIR = Path("evaluation/results")
REPORT_PATH = RESULTS_DIR / "performance_baseline.json"

BENCHMARK_QUERIES = [
    "What is the remote work allowance?",
    "How many days per week can employees work remotely?",
    "How should employees connect to company systems remotely?",
    "What is the company's password policy?",
]


def configure_benchmark_tracing():
    exporter = InMemorySpanExporter()

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "enterprise-rag-platform"}
        )
    )

    provider.add_span_processor(
        SimpleSpanProcessor(exporter)
    )

    trace.set_tracer_provider(provider)

    return exporter


def percentile(values, percentage):
    if not values:
        return None

    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * percentage / 100

    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return ordered[lower]

    fraction = position - lower

    return (
        ordered[lower]
        + (ordered[upper] - ordered[lower]) * fraction
    )


def extract_query_metrics(exporter):
    root_spans = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "rag.query"
    ]

    if len(root_spans) != 1:
        raise RuntimeError(
            "Expected exactly one rag.query span, "
            f"found {len(root_spans)}"
        )

    attributes = root_spans[0].attributes or {}

    integer_fields = [
        "generation_attempts",
        "generation_input_tokens",
        "generation_cached_input_tokens",
        "generation_output_tokens",
        "embedding_api_calls",
        "embedding_cache_hits",
        "embedding_input_tokens",
        "total_api_tokens",
    ]

    cost_fields = [
        "generation_cost_usd",
        "embedding_cost_usd",
        "total_cost_usd",
    ]

    metrics = {
        "cache_status": attributes.get(
            "rag.cache_status",
            "UNKNOWN",
        )
    }

    for field in integer_fields:
        metrics[field] = int(
            attributes.get(f"rag.{field}", 0)
        )

    for field in cost_fields:
        metrics[field] = str(
            attributes.get(f"rag.{field}", 0)
        )

    return metrics


async def execute_query(
    query,
    model,
    user_context,
    exporter,
    use_cache,
    label,
):
    exporter.clear()

    start = perf_counter()

    try:
        result = await run_rag(
            query=query,
            model=model,
            user_context=user_context,
            use_cache=use_cache,
        )

        latency_ms = (perf_counter() - start) * 1000
        metrics = extract_query_metrics(exporter)

        return {
            "label": label,
            "query": query,
            "success": True,
            "latency_ms": round(latency_ms, 2),
            "answered": bool(result["answer"]),
            "citation_count": len(result["citations"]),
            **metrics,
        }

    except Exception as exc:
        latency_ms = (perf_counter() - start) * 1000

        return {
            "label": label,
            "query": query,
            "success": False,
            "latency_ms": round(latency_ms, 2),
            "cache_status": "ERROR",
            "error": str(exc),
        }


def summarize_group(results):
    successful = [
        item for item in results
        if item["success"]
    ]

    latencies = [
        item["latency_ms"]
        for item in successful
    ]

    costs = [
        Decimal(item["total_cost_usd"])
        for item in successful
    ]

    total_cost = sum(costs, Decimal("0"))

    attempts = sum(
        item["generation_attempts"]
        for item in successful
    )

    return {
        "total_queries": len(results),
        "successful_queries": len(successful),
        "failed_queries": len(results) - len(successful),
        "latency_ms": {
            "mean": (
                round(statistics.mean(latencies), 2)
                if latencies else None
            ),
            "p50": (
                round(percentile(latencies, 50), 2)
                if latencies else None
            ),
            "p95": (
                round(percentile(latencies, 95), 2)
                if latencies else None
            ),
            "min": (
                round(min(latencies), 2)
                if latencies else None
            ),
            "max": (
                round(max(latencies), 2)
                if latencies else None
            ),
        },
        "total_api_tokens": sum(
            item["total_api_tokens"]
            for item in successful
        ),
        "total_generation_attempts": attempts,
        "additional_generation_attempts": sum(
            max(item["generation_attempts"] - 1, 0)
            for item in successful
        ),
        "total_embedding_api_calls": sum(
            item["embedding_api_calls"]
            for item in successful
        ),
        "total_embedding_cache_hits": sum(
            item["embedding_cache_hits"]
            for item in successful
        ),
        "total_cost_usd": str(total_cost),
        "average_cost_usd": (
            str(total_cost / len(successful))
            if successful else None
        ),
    }


def summarize_by_cache_status(results):
    grouped = defaultdict(list)

    for item in results:
        grouped[item["cache_status"]].append(item)

    return {
        status: summarize_group(items)
        for status, items in sorted(grouped.items())
    }


def summarize_by_label(results):
    grouped = defaultdict(list)

    for item in results:
        grouped[item["label"]].append(item)

    return {
        label: summarize_group(items)
        for label, items in sorted(grouped.items())
    }


async def run_benchmark(args, exporter):
    user_context = UserContext(
        user_id="performance-runner",
        tenant_id=args.tenant,
        role=Role.EMPLOYEE,
    )

    results = []
    queries = BENCHMARK_QUERIES[:args.limit]

    for index, query in enumerate(queries, start=1):
        print(f"\n[{index}/{len(queries)}] {query}")

        bypassed = await execute_query(
            query=query,
            model=args.model,
            user_context=user_context,
            exporter=exporter,
            use_cache=False,
            label="result_cache_bypassed",
        )

        results.append(bypassed)

        print(
            "BYPASSED:",
            f"{bypassed['latency_ms']:.2f} ms",
            "PASS" if bypassed["success"] else "FAIL",
        )

        if not args.compare_cache:
            continue

        warmup = await execute_query(
            query=query,
            model=args.model,
            user_context=user_context,
            exporter=exporter,
            use_cache=True,
            label="cache_warmup",
        )

        results.append(warmup)

        print(
            "WARMUP:",
            f"{warmup['latency_ms']:.2f} ms",
            warmup["cache_status"],
        )

        cache_check = await execute_query(
            query=query,
            model=args.model,
            user_context=user_context,
            exporter=exporter,
            use_cache=True,
            label="result_cache_hit_check",
        )

        results.append(cache_check)

        print(
            "CACHE:",
            f"{cache_check['latency_ms']:.2f} ms",
            cache_check["cache_status"],
        )

    return results


def print_group_summary(title, summary):
    latency = summary["latency_ms"]

    print(f"\n--- {title} ---")
    print("Queries:", summary["total_queries"])
    print("Successful:", summary["successful_queries"])
    print("Failed:", summary["failed_queries"])
    print("Mean latency:", latency["mean"], "ms")
    print("p50 latency:", latency["p50"], "ms")
    print("p95 latency:", latency["p95"], "ms")
    print("Total API tokens:", summary["total_api_tokens"])
    print(
        "Generation attempts:",
        summary["total_generation_attempts"],
    )
    print(
        "Additional generation attempts:",
        summary["additional_generation_attempts"],
    )
    print(
        "Embedding API calls:",
        summary["total_embedding_api_calls"],
    )
    print(
        "Embedding cache hits:",
        summary["total_embedding_cache_hits"],
    )
    print(
        "Total estimated cost:",
        summary["total_cost_usd"],
        "USD",
    )
    print(
        "Average estimated cost:",
        summary["average_cost_usd"],
        "USD",
    )


def print_summary(report):
    print("\n" + "=" * 60)
    print("PERFORMANCE BENCHMARK SUMMARY")
    print("=" * 60)

    overall = report["summary"]

    print("Total executions:", overall["total_queries"])
    print("Successful:", overall["successful_queries"])
    print("Failed:", overall["failed_queries"])
    print(
        "Total estimated API cost:",
        overall["total_cost_usd"],
        "USD",
    )

    print("\nCACHE STATUS BREAKDOWN")

    for status, summary in report["by_cache_status"].items():
        print_group_summary(status, summary)

    print("\nEXECUTION LABEL BREAKDOWN")

    for label, summary in report["by_label"].items():
        print(
            f"{label}: "
            f"{summary['total_queries']} executions, "
            f"mean={summary['latency_ms']['mean']} ms"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark the Enterprise RAG pipeline."
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
    )

    parser.add_argument(
        "--tenant",
        default=DEFAULT_TENANT,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--compare-cache",
        action="store_true",
    )

    return parser.parse_args()


async def main():
    args = parse_args()

    if args.limit < 1:
        raise ValueError("--limit must be at least 1")

    exporter = configure_benchmark_tracing()

    results = await run_benchmark(
        args=args,
        exporter=exporter,
    )

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "tenant": args.tenant,
        "compare_cache": args.compare_cache,
        "summary": summarize_group(results),
        "by_cache_status": summarize_by_cache_status(results),
        "by_label": summarize_by_label(results),
        "results": results,
    }

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_PATH.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print_summary(report)
    print("\nReport saved:", REPORT_PATH)


if __name__ == "__main__":
    asyncio.run(main())
