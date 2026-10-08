import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from src.auth.models import Role, UserContext
from src.rag.pipeline import run_rag


DATASET_PATH = Path(__file__).parent / "dataset.json"
RESULTS_DIR = Path(__file__).parent / "results"
REPORT_PATH = RESULTS_DIR / "baseline.json"

MODEL = "gpt-4o-mini"

NO_ANSWER_MESSAGE = (
    "I don't have enough information in the provided "
    "sources to answer this question."
)


def load_dataset():
    # Load evaluation cases from the JSON dataset
    with open(DATASET_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def check_expected_answer(expected_answer, actual_answer):
    # Skip answer matching for no-answer cases
    if expected_answer is None:
        return None

    # Support both a single answer and multiple alternatives
    if isinstance(expected_answer, str):
        expected_answer = [expected_answer]

    actual_answer = actual_answer.lower()

    return any(
        answer.lower() in actual_answer
        for answer in expected_answer
    )


def check_no_answer(should_answer, actual_answer, citations):
    # Skip no-answer checking for answerable questions
    if should_answer:
        return None

    return (
        actual_answer.strip() == NO_ANSWER_MESSAGE
        and len(citations) == 0
    )


def check_citation_source(expected_filename, citations):
    # Skip citation checking when no source is expected
    if expected_filename is None:
        return None

    actual_filenames = {
        source["filename"]
        for citation in citations
        for source in citation["sources"]
    }

    return (
        len(actual_filenames) > 0
        and actual_filenames == {expected_filename}
    )


async def evaluate_case(case):
    # Print case information before running the RAG pipeline
    print("\n" + "=" * 55, flush=True)
    print(f"Case: {case['id']}", flush=True)
    print(f"Tenant: {case['tenant_id']}", flush=True)
    print(f"Query: {case['query']}", flush=True)
    print("=" * 55, flush=True)

    user_context = UserContext(
        user_id="evaluation-runner",
        tenant_id=case["tenant_id"],
        role=Role.EMPLOYEE
    )

    print("\n--- RAG Pipeline ---", flush=True)

    result = await run_rag(
        query=case["query"],
        model=MODEL,
        user_context=user_context,
        use_cache=False
    )

    answer_check = check_expected_answer(
        expected_answer=case["expected_answer"],
        actual_answer=result["answer"]
    )

    no_answer_check = check_no_answer(
        should_answer=case["should_answer"],
        actual_answer=result["answer"],
        citations=result["citations"]
    )

    citation_source_check = check_citation_source(
        expected_filename=case["expected_filename"],
        citations=result["citations"]
    )

    # None means the check was not applicable
    applicable_checks = [
        check
        for check in [
            answer_check,
            no_answer_check,
            citation_source_check
        ]
        if check is not None
    ]

    passed = (
        len(applicable_checks) > 0
        and all(applicable_checks)
    )

    print("\n--- Evaluation Results ---")
    print(f"Expected answer: {case['expected_answer']}")
    print(f"Actual answer: {result['answer']}")
    print(f"Citations: {result['citations']}")
    print(f"Answer Check: {answer_check}")
    print(f"No-answer Check: {no_answer_check}")
    print(f"Citation Source Check: {citation_source_check}")
    print(f"Overall Pass: {passed}")

    # Return a structured result for the baseline report
    return {
        "case_id": case["id"],
        "tenant_id": case["tenant_id"],
        "query": case["query"],
        "expected_answer": case["expected_answer"],
        "actual_answer": result["answer"],
        "citations": result["citations"],
        "checks": {
            "answer": answer_check,
            "no_answer": no_answer_check,
            "citation_source": citation_source_check
        },
        "passed": passed
    }


async def main():
    dataset = load_dataset()

    print(f"Loaded {len(dataset)} evaluation cases")

    results = []

    # Evaluate each case sequentially
    for case in dataset:
        case_result = await evaluate_case(case)
        results.append(case_result)

    total_cases = len(results)
    passed_cases = sum(
        1 for result in results if result["passed"]
    )
    failed_cases = total_cases - passed_cases

    pass_rate = (
        passed_cases / total_cases
        if total_cases > 0
        else 0.0
    )

    # Build the baseline report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "result_cache_enabled": False,
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "pass_rate": round(pass_rate, 4),
        "results": results
    }

    # Create the results directory if it does not exist
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Save the report as formatted JSON
    with open(REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n" + "=" * 55)
    print("EVALUATION SUMMARY")
    print("=" * 55)
    print(f"Total Cases: {total_cases}")
    print(f"Passed Cases: {passed_cases}")
    print(f"Failed Cases: {failed_cases}")
    print(f"Pass Rate: {pass_rate:.2%}")
    print(f"Report Saved: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
