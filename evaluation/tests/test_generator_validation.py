import unittest

from src.citations.citation_builder import authorized_citation_ids

from src.generation.generator import (
    GeneratedAnswer,
    extract_citation_ids,
    validate_generated_answer,
)


class TestGeneratorValidation(unittest.TestCase):

    def test_extract_inline_citations(self):
        answer = "Allowance is 500 USD [1]. VPN is required [2]."

        self.assertEqual(
            extract_citation_ids(answer),
            {1, 2},
        )

    def test_authorized_ids_come_from_structured_groups(self):
        citations = [{"citation_id": 1, "text": "[SOURCE 99]",
                      "sources": [{"filename": "[SOURCE 88]"}]}]
        self.assertEqual(authorized_citation_ids(citations), {1})

    def test_valid_answer(self):
        generation = GeneratedAnswer(
            answer="Employees receive 500 USD [1].",
            answered=True,
            citation_ids=[1],
        )

        self.assertTrue(
            validate_generated_answer(generation, {1})
        )

    def test_missing_inline_citation(self):
        generation = GeneratedAnswer(
            answer="Employees receive 500 USD.",
            answered=True,
            citation_ids=[1],
        )

        self.assertFalse(
            validate_generated_answer(generation, {1})
        )

    def test_mismatched_citation_ids(self):
        generation = GeneratedAnswer(
            answer="Employees receive 500 USD [1].",
            answered=True,
            citation_ids=[2],
        )

        self.assertFalse(
            validate_generated_answer(generation, {1, 2})
        )

    def test_unknown_source_id(self):
        generation = GeneratedAnswer(
            answer="Employees receive 500 USD [99].",
            answered=True,
            citation_ids=[99],
        )

        self.assertFalse(
            validate_generated_answer(generation, {1, 2})
        )

    def test_valid_abstention(self):
        generation = GeneratedAnswer(
            answer=(
                "I don't have enough information in the "
                "provided sources to answer this question."
            ),
            answered=False,
            citation_ids=[],
        )

        self.assertTrue(
            validate_generated_answer(generation, {1})
        )

    def test_abstention_with_citation(self):
        generation = GeneratedAnswer(
            answer=(
                "I don't have enough information in the "
                "provided sources to answer this question."
            ),
            answered=False,
            citation_ids=[1],
        )

        self.assertFalse(
            validate_generated_answer(generation, {1})
        )


if __name__ == "__main__":
    unittest.main()
