import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.embedder import create_embeddings
from src.retrieval.deduplicator import deduplicate_exact
from src.retrieval.hybrid_retriever import hybrid_search
from src.retrieval.mmr import select_with_mmr
from src.retrieval.reranker import rerank

from evaluation.retrieval_runner import (
    load_dataset,
    extract_retrieved_source_groups,
    calculate_context_metrics,
)


RESULTS_DIR = Path(__file__).parent / "results"
REPORT_PATH = RESULTS_DIR / "ablation_results.json"

RETRIEVAL_LIMIT = 5
FINAL_LIMIT = 3
LAMBDA_VALUE = 0.5

STAGES = [
    "hybrid",
    "reranking",
    "mmr",
]


def evaluate_stage(candidates, relevant_ids):
    source_groups = extract_retrieved_source_groups(candidates)

    precision, recall, rr = calculate_context_metrics(
        source_groups=source_groups,
        relevant_ids=relevant_ids,
        k=FINAL_LIMIT,
    )

    return {
        "retrieved_source_groups": source_groups,
        "precision_at_k": precision,
        "recall_at_k": recall,
        "reciprocal_rank": rr,
    }


async def evaluate_case(case):
    query = case["query"]
    tenant_id = case["tenant_id"]
    relevant_ids = case["relevant_chunk_ids"]

    print(f"\nEvaluating {case['id']}: {query}", flush=True)

    # A. Hybrid Search + RRF
    hybrid_candidates = await hybrid_search(
        query=query,
        tenant_id=tenant_id,
        retrieval_limit=RETRIEVAL_LIMIT,
        final_limit=RETRIEVAL_LIMIT,
    )

    # Deduplicate once, before comparing stages.
    unique_candidates = deduplicate_exact(hybrid_candidates)

    hybrid_top_k = unique_candidates[:FINAL_LIMIT]

    hybrid_metrics = evaluate_stage(
        hybrid_top_k,
        relevant_ids,
    )

    if not unique_candidates:
        return {
            "case_id": case["id"],
            "tenant_id": tenant_id,
            "query": query,
            "hybrid": hybrid_metrics,
            "reranking": evaluate_stage([], relevant_ids),
            "mmr": evaluate_stage([], relevant_ids),
        }

    # B. Cross-Encoder Reranking
    reranked_candidates = rerank(
        query=query,
        candidates=unique_candidates,
        limit=len(unique_candidates),
    )

    reranking_top_k = reranked_candidates[:FINAL_LIMIT]

    reranking_metrics = evaluate_stage(
        reranking_top_k,
        relevant_ids,
    )

    # C. MMR Context Selection
    # Only the MMR stage needs candidate embeddings.
    mmr_candidates = [
        dict(candidate)
        for candidate in reranked_candidates
    ]

    texts = [
        candidate["text"]
        for candidate in mmr_candidates
    ]

    embeddings = await create_embeddings(texts)

    for candidate, embedding in zip(
        mmr_candidates,
        embeddings,
    ):
        candidate["embedding"] = embedding

    mmr_top_k = select_with_mmr(
        candidates=mmr_candidates,
        limit=FINAL_LIMIT,
        lambda_value=LAMBDA_VALUE,
    )

    mmr_metrics = evaluate_stage(
        mmr_top_k,
        relevant_ids,
    )

    print(
        "  Reciprocal Rank | "
        f"Hybrid: {hybrid_metrics['reciprocal_rank']:.3f} | "
        f"Reranking: {reranking_metrics['reciprocal_rank']:.3f} | "
        f"MMR: {mmr_metrics['reciprocal_rank']:.3f}"
    )

    return {
        "case_id": case["id"],
        "tenant_id": tenant_id,
        "query": query,
        "hybrid": hybrid_metrics,
        "reranking": reranking_metrics,
        "mmr": mmr_metrics,
    }


def summarize_stage(results, stage):
    stage_results = [
        result[stage]
        for result in results
    ]

    total = len(stage_results)

    if total == 0:
        return {
            "mean_precision_at_k": 0.0,
            "mean_recall_at_k": None,
            "mrr": 0.0,
        }

    valid_recalls = [
        result["recall_at_k"]
        for result in stage_results
        if result["recall_at_k"] is not None
    ]

    return {
        "mean_precision_at_k": round(
            sum(
                result["precision_at_k"]
                for result in stage_results
            ) / total,
            4,
        ),
        "mean_recall_at_k": round(
            sum(valid_recalls) / len(valid_recalls),
            4,
        ) if valid_recalls else None,
        "mrr": round(
            sum(
                result["reciprocal_rank"]
                for result in stage_results
            ) / total,
            4,
        ),
    }


async def main():
    dataset = load_dataset()

    print(f"Loaded {len(dataset)} evaluation cases")

    results = []

    for case in dataset:
        result = await evaluate_case(case)
        results.append(result)

    summary = {
        stage: summarize_stage(results, stage)
        for stage in STAGES
    }

    report = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "retrieval_limit": RETRIEVAL_LIMIT,
        "final_limit": FINAL_LIMIT,
        "lambda_value": LAMBDA_VALUE,
        "total_cases": len(results),
        "summary": summary,
        "results": results,
    }

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        REPORT_PATH,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print("\n" + "=" * 65)
    print("PIPELINE ABLATION RESULTS")
    print("=" * 65)

    print(
        f"{'Stage':<15}"
        f"{'Precision@3':<16}"
        f"{'Recall@3':<14}"
        f"{'MRR':<10}"
    )

    print("-" * 65)

    for stage in STAGES:
        metrics = summary[stage]

        recall = metrics["mean_recall_at_k"]

        recall_text = (
            f"{recall:.4f}"
            if recall is not None
            else "N/A"
        )

        print(
            f"{stage:<15}"
            f"{metrics['mean_precision_at_k']:<16.4f}"
            f"{recall_text:<14}"
            f"{metrics['mrr']:<10.4f}"
        )

    print(f"\nReport saved: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
