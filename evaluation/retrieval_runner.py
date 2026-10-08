import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from src.retrieval.context_selector import select_context


DATASET_PATH = Path(__file__).parent / "retrieval_dataset.json"
RESULTS_DIR = Path(__file__).parent / "results"
REPORT_PATH = RESULTS_DIR / "retrieval_baseline.json"

RETRIEVAL_LIMIT = 5
FINAL_LIMIT = 3
LAMBDA_VALUE = 0.5


def load_dataset():
    with open(DATASET_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def build_chunk_id(source):
    return f"{source['document_id']}:{source['chunk_id']}"


def extract_retrieved_source_groups(contexts):
    """
    Extract all source IDs for each selected context.

    One context may contain multiple sources after
    exact deduplication.

    Each inner list represents ONE retrieved context.
    """
    source_groups = []

    for context in contexts:
        sources = context.get("sources") or [context]

        source_ids = list(
            dict.fromkeys(
                build_chunk_id(source)
                for source in sources
            )
        )

        source_groups.append(source_ids)

    return source_groups


def calculate_context_metrics(source_groups, relevant_ids, k):
    """
    Calculate retrieval metrics using all source IDs.

    Precision@K:
        Fraction of the first K contexts that match
        at least one relevant source.

    Recall@K:
        Fraction of ground-truth source IDs found
        in the first K contexts.

    Reciprocal Rank:
        Inverse rank of the first context matching
        at least one relevant source.
    """
    if k <= 0:
        raise ValueError("k must be greater than 0")

    relevant = set(relevant_ids)
    top_k = source_groups[:k]

    matched_relevant = set()
    relevant_context_count = 0
    first_relevant_rank = None

    for rank, source_ids in enumerate(top_k, start=1):
        matches = set(source_ids) & relevant

        if matches:
            relevant_context_count += 1
            matched_relevant.update(matches)

            if first_relevant_rank is None:
                first_relevant_rank = rank

    precision = relevant_context_count / k

    recall = (
        len(matched_relevant) / len(relevant)
        if relevant else None
    )

    reciprocal_rank = (
        1.0 / first_relevant_rank
        if first_relevant_rank is not None else 0.0
    )

    return precision, recall, reciprocal_rank


async def evaluate_case(case):
    print("\n" + "=" * 55, flush=True)
    print(f"Case: {case['id']}", flush=True)
    print(f"Tenant: {case['tenant_id']}", flush=True)
    print(f"Query: {case['query']}", flush=True)
    print("=" * 55, flush=True)

    contexts = await select_context(
        query=case["query"],
        tenant_id=case["tenant_id"],
        retrieval_limit=RETRIEVAL_LIMIT,
        final_limit=FINAL_LIMIT,
        lambda_value=LAMBDA_VALUE,
    )

    source_groups = extract_retrieved_source_groups(contexts)

    relevant_ids = case["relevant_chunk_ids"]

    precision, recall, rr = calculate_context_metrics(
        source_groups=source_groups,
        relevant_ids=relevant_ids,
        k=FINAL_LIMIT,
    )

    # Representative IDs for backward-compatible reporting.
    retrieved_ids = [
        group[0] for group in source_groups
    ]

    print("\n--- Retrieval Results ---")
    print(f"Retrieved IDs: {retrieved_ids}")
    print(f"Retrieved Source Groups: {source_groups}")
    print(f"Relevant IDs: {relevant_ids}")
    print(f"Precision@{FINAL_LIMIT}: {precision:.4f}")

    if recall is not None:
        print(f"Recall@{FINAL_LIMIT}: {recall:.4f}")
    else:
        print("Recall: N/A")

    print(f"Reciprocal Rank: {rr:.4f}")

    return {
        "case_id": case["id"],
        "tenant_id": case["tenant_id"],
        "query": case["query"],
        "retrieved_ids": retrieved_ids,
        "retrieved_source_groups": source_groups,
        "relevant_ids": relevant_ids,
        "precision_at_k": precision,
        "recall_at_k": recall,
        "reciprocal_rank": rr,
    }


async def main():
    dataset = load_dataset()

    print(f"Loaded {len(dataset)} retrieval evaluation cases")

    results = []

    for case in dataset:
        result = await evaluate_case(case)
        results.append(result)

    total_cases = len(results)

    mean_precision = (
        sum(item["precision_at_k"] for item in results) / total_cases
        if total_cases else 0.0
    )

    valid_recalls = [
        item["recall_at_k"]
        for item in results
        if item["recall_at_k"] is not None
    ]

    mean_recall = (
        sum(valid_recalls) / len(valid_recalls)
        if valid_recalls else None
    )

    mrr = (
        sum(item["reciprocal_rank"] for item in results) / total_cases
        if total_cases else 0.0
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retrieval_limit": RETRIEVAL_LIMIT,
        "final_limit": FINAL_LIMIT,
        "lambda_value": LAMBDA_VALUE,
        "total_cases": total_cases,
        "mean_precision_at_k": round(mean_precision, 4),
        "mean_recall_at_k": (
            round(mean_recall, 4)
            if mean_recall is not None else None
        ),
        "mrr": round(mrr, 4),
        "results": results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print("\n" + "=" * 55)
    print("RETRIEVAL EVALUATION SUMMARY")
    print("=" * 55)
    print(f"Total Cases: {total_cases}")
    print(f"Mean Precision@{FINAL_LIMIT}: {mean_precision:.4f}")

    if mean_recall is not None:
        print(f"Mean Recall@{FINAL_LIMIT}: {mean_recall:.4f}")
    else:
        print("Mean Recall: N/A")

    print(f"MRR: {mrr:.4f}")
    print(f"Report Saved: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
