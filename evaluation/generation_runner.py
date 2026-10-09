import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.auth.models import Role, UserContext
from src.rag.pipeline import run_rag
from evaluation.generation_metrics import evaluate_generation


load_dotenv()

DATASET_PATH = Path(__file__).parent / "generation_dataset.json"
RESULTS_DIR = Path(__file__).parent / "results"
REPORT_PATH = RESULTS_DIR / "generation_baseline.json"

MODEL = os.getenv("GENERATION_MODEL", "gpt-4.1-mini")

RETRIEVAL_LIMIT = 5
FINAL_LIMIT = 3
LAMBDA_VALUE = 0.5


def load_dataset():
    with open(DATASET_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


async def evaluate_case(case):
    print("\n" + "=" * 60, flush=True)
    print(f"Case: {case['id']}", flush=True)
    print(f"Tenant: {case['tenant_id']}", flush=True)
    print(f"Query: {case['query']}", flush=True)
    print("=" * 60, flush=True)

    user_context = UserContext(
        user_id="evaluation-runner",
        tenant_id=case["tenant_id"],
        role=Role.EMPLOYEE,
    )

    result = await run_rag(
        query=case["query"],
        model=MODEL,
        user_context=user_context,
        retrieval_limit=RETRIEVAL_LIMIT,
        final_limit=FINAL_LIMIT,
        lambda_value=LAMBDA_VALUE,
        use_cache=False,
    )

    evaluation = evaluate_generation(
        result=result,
        case=case,
    )

    status = "PASS" if evaluation["passed"] else "FAIL"

    print("\nGenerated Answer:")
    print(result["answer"])

    print("\nEvaluation Checks:")
    for check_name, check_result in evaluation["checks"].items():
        check_status = (
            "PASS" if check_result["passed"] else "FAIL"
        )
        print(f"  {check_name}: {check_status}")

    print(f"\nOverall: {status}", flush=True)

    return {
        "case_id": case["id"],
        "category": case["category"],
        "tenant_id": case["tenant_id"],
        "query": case["query"],
        "expected_answer": case["expected_answer"],
        "generated_answer": result["answer"],
        "citations": result["citations"],
        "status": status,
        "error": None,
        "evaluation": evaluation,
    }


def build_error_result(case, exc):
    """
    Convert a case-level exception into a reportable ERROR result.
    """
    return {
        "case_id": case["id"],
        "category": case["category"],
        "tenant_id": case["tenant_id"],
        "query": case["query"],
        "expected_answer": case["expected_answer"],
        "generated_answer": None,
        "citations": [],
        "status": "ERROR",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "evaluation": None,
    }


def save_report(report):
    """
    Save the current evaluation report as JSON.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False,
        )


async def main():
    dataset = load_dataset()

    print(f"Loaded {len(dataset)} generation evaluation cases")
    print(f"Model: {MODEL}")

    results = []

    for case in dataset:
        try:
            result = await evaluate_case(case)

        except Exception as exc:
            print(
                f"\nOverall: ERROR ({case['id']})",
                flush=True,
            )
            print(f"Error Type: {type(exc).__name__}")
            print(f"Error Message: {exc}")

            result = build_error_result(
                case=case,
                exc=exc,
            )

        results.append(result)

    total_cases = len(results)

    passed_cases = sum(
        result["status"] == "PASS"
        for result in results
    )

    failed_cases = sum(
        result["status"] == "FAIL"
        for result in results
    )

    error_cases = sum(
        result["status"] == "ERROR"
        for result in results
    )

    pass_rate = (
        passed_cases / total_cases
        if total_cases
        else 0.0
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "retrieval_limit": RETRIEVAL_LIMIT,
        "final_limit": FINAL_LIMIT,
        "lambda_value": LAMBDA_VALUE,
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "error_cases": error_cases,
        "pass_rate": round(pass_rate, 4),
        "results": results,
    }

    save_report(report)

    print("\n" + "=" * 60)
    print("GENERATION EVALUATION SUMMARY")
    print("=" * 60)
    print(f"Total Cases: {total_cases}")
    print(f"Passed Cases: {passed_cases}")
    print(f"Failed Cases: {failed_cases}")
    print(f"Error Cases: {error_cases}")
    print(f"Pass Rate: {pass_rate:.4f}")
    print(f"Report Saved: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
