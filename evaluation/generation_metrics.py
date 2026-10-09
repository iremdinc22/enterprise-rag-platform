import re


def normalize_text(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    return " ".join(text.casefold().split())


def check_expected_facts(answer, expected_facts):
    """
    Check whether all required fact phrases appear in the answer.
    This is a lexical check, not semantic correctness.
    """
    normalized_answer = normalize_text(answer)

    missing_facts = [
        fact
        for fact in expected_facts
        if normalize_text(fact) not in normalized_answer
    ]

    return {
        "passed": len(missing_facts) == 0,
        "missing_facts": missing_facts,
    }


def check_forbidden_facts(answer, forbidden_facts):
    """
    Check whether any prohibited fact phrase appears in the answer.
    """
    normalized_answer = normalize_text(answer)

    found_facts = [
        fact
        for fact in forbidden_facts
        if normalize_text(fact) in normalized_answer
    ]

    return {
        "passed": len(found_facts) == 0,
        "found_forbidden_facts": found_facts,
    }


def extract_citation_ids(answer):
    """
    Extract citation markers such as [1] and [2] from the answer.
    """
    return [
        int(match)
        for match in re.findall(r"\[(\d+)\]", answer)
    ]


def check_citation_presence(answer, citations, should_answer):
    """
    For answerable cases, require at least one citation marker
    and verify that every marker exists in the response metadata.

    For abstention cases, require no citation markers or metadata.
    """
    cited_ids = set(extract_citation_ids(answer))

    available_ids = {
        citation["citation_id"]
        for citation in citations
    }

    if not should_answer:
        passed = not cited_ids and not available_ids
    else:
        passed = (
            bool(cited_ids)
            and cited_ids == available_ids
        )

    return {
        "passed": passed,
        "cited_ids": sorted(cited_ids),
        "available_ids": sorted(available_ids),
    }


def check_expected_documents(citations, expected_document_ids):
    """
    Verify that every expected document is represented
    in at least one returned citation source.
    """
    cited_document_ids = {
        source["document_id"]
        for citation in citations
        for source in citation.get("sources", [])
    }

    missing_document_ids = [
        document_id
        for document_id in expected_document_ids
        if document_id not in cited_document_ids
    ]

    return {
        "passed": len(missing_document_ids) == 0,
        "missing_document_ids": missing_document_ids,
        "cited_document_ids": sorted(cited_document_ids),
    }


def check_abstention(answer, should_answer):
    """
    Check the fixed abstention message required by
    the current generation prompt.
    """
    abstention_message = (
        "I don't have enough information in the provided "
        "sources to answer this question."
    )

    is_abstention = (
        normalize_text(answer)
        == normalize_text(abstention_message)
    )

    return {
        "passed": (
            not is_abstention
            if should_answer
            else is_abstention
        ),
        "is_abstention": is_abstention,
    }


def evaluate_generation(result, case):
    """
    Evaluate a RAG response against one dataset case.
    """
    answer = result["answer"]
    citations = result["citations"]

    checks = {
        "expected_facts": check_expected_facts(
            answer,
            case["expected_facts"],
        ),
        "forbidden_facts": check_forbidden_facts(
            answer,
            case["forbidden_facts"],
        ),
        "citation_presence": check_citation_presence(
            answer,
            citations,
            case["should_answer"],
        ),
        "expected_documents": check_expected_documents(
            citations,
            case["expected_document_ids"],
        ),
        "abstention": check_abstention(
            answer,
            case["should_answer"],
        ),
    }

    passed = all(
        check["passed"]
        for check in checks.values()
    )

    return {
        "case_id": case["id"],
        "passed": passed,
        "checks": checks,
    }
