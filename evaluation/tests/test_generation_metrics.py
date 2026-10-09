import unittest

from evaluation.generation_metrics import (
    check_expected_facts,
    check_forbidden_facts,
    check_citation_presence,
    check_expected_documents,
    check_abstention,
    evaluate_generation,
)


class TestGenerationMetrics(unittest.TestCase):

    def test_expected_fact_present(self):
        result = check_expected_facts(
            "Employees receive 500 USD.",
            ["500 USD"],
        )
        self.assertTrue(result["passed"])

    def test_expected_fact_missing(self):
        result = check_expected_facts(
            "Employees receive 2000 USD.",
            ["500 USD"],
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["missing_facts"],
            ["500 USD"],
        )

    def test_forbidden_fact_detected(self):
        result = check_forbidden_facts(
            "Employees receive 2000 USD.",
            ["2000 USD"],
        )
        self.assertFalse(result["passed"])

    def test_valid_citation(self):
        result = check_citation_presence(
            answer="Employees receive 500 USD [1].",
            citations=[{"citation_id": 1, "sources": []}],
            should_answer=True,
        )
        self.assertTrue(result["passed"])

    def test_missing_citation(self):
        result = check_citation_presence(
            answer="Employees receive 500 USD.",
            citations=[],
            should_answer=True,
        )
        self.assertFalse(result["passed"])

    def test_invalid_citation_id(self):
        result = check_citation_presence(
            answer="Employees receive 500 USD [2].",
            citations=[{"citation_id": 1, "sources": []}],
            should_answer=True,
        )
        self.assertFalse(result["passed"])

    def test_expected_document_present(self):
        result = check_expected_documents(
            citations=[
                {
                    "citation_id": 1,
                    "sources": [
                        {"document_id": "remote-work-policy"}
                    ],
                }
            ],
            expected_document_ids=["remote-work-policy"],
        )
        self.assertTrue(result["passed"])

    def test_expected_document_missing(self):
        result = check_expected_documents(
            citations=[],
            expected_document_ids=["remote-work-policy"],
        )
        self.assertFalse(result["passed"])

    def test_correct_abstention(self):
        result = check_abstention(
            answer=(
                "I don't have enough information in the "
                "provided sources to answer this question."
            ),
            should_answer=False,
        )
        self.assertTrue(result["passed"])

    def test_unnecessary_abstention(self):
        result = check_abstention(
            answer=(
                "I don't have enough information in the "
                "provided sources to answer this question."
            ),
            should_answer=True,
        )
        self.assertFalse(result["passed"])

    def test_complete_generation_evaluation(self):
        case = {
            "id": "generation-test",
            "expected_facts": ["500 USD"],
            "forbidden_facts": ["2000 USD"],
            "expected_document_ids": ["remote-work-policy"],
            "should_answer": True,
        }

        rag_result = {
            "answer": "Employees receive 500 USD [1].",
            "citations": [
                {
                    "citation_id": 1,
                    "sources": [
                        {"document_id": "remote-work-policy"}
                    ],
                }
            ],
        }

        result = evaluate_generation(rag_result, case)

        self.assertTrue(result["passed"])
        self.assertEqual(result["case_id"], "generation-test")


if __name__ == "__main__":
    unittest.main()
