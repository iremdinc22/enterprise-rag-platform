import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.generation.generator import (
    GeneratedAnswer,
    generate_answer,
)


TEST_CONTEXT = """
[SOURCE 1]
ACME employees receive 20 days of annual leave.
""".strip()


def mock_response(answer, answered, citation_ids):
    return SimpleNamespace(
        output_parsed=GeneratedAnswer(
            answer=answer,
            answered=answered,
            citation_ids=citation_ids,
        )
    )


class TestGeneratorRetry(unittest.IsolatedAsyncioTestCase):

    @patch("src.generation.generator.client.responses.parse")
    async def test_valid_first_attempt(self, mock_parse):
        mock_parse.return_value = mock_response(
            answer="ACME employees receive 20 days of annual leave [1].",
            answered=True,
            citation_ids=[1],
        )

        result = await generate_answer(
            query="How many days of annual leave do ACME employees receive?",
            context=TEST_CONTEXT,
            model="gpt-4.1-mini",
        )

        self.assertEqual(result.citation_ids, [1])
        self.assertIn("[1]", result.answer)
        self.assertEqual(mock_parse.await_count, 1)

    @patch("src.generation.generator.client.responses.parse")
    async def test_retry_after_missing_citation(self, mock_parse):
        mock_parse.side_effect = [
            mock_response(
                answer="ACME employees receive 20 days of annual leave.",
                answered=True,
                citation_ids=[1],
            ),
            mock_response(
                answer="ACME employees receive 20 days of annual leave [1].",
                answered=True,
                citation_ids=[1],
            ),
        ]

        result = await generate_answer(
            query="How many days of annual leave do ACME employees receive?",
            context=TEST_CONTEXT,
            model="gpt-4.1-mini",
        )

        self.assertIn("[1]", result.answer)
        self.assertEqual(mock_parse.await_count, 2)

        retry_input = mock_parse.await_args_list[1].kwargs["input"]

        self.assertIn(
            "The factual answer contains no inline citations",
            retry_input,
        )

        self.assertIn(
            "Previous answer:",
            retry_input,
        )

    @patch("src.generation.generator.client.responses.parse")
    async def test_failure_after_two_invalid_attempts(self, mock_parse):
        invalid_response = mock_response(
            answer="ACME employees receive 20 days of annual leave.",
            answered=True,
            citation_ids=[1],
        )

        mock_parse.side_effect = [
            invalid_response,
            invalid_response,
        ]

        with self.assertRaisesRegex(
            ValueError,
            "Generation failed citation consistency validation",
        ):
            await generate_answer(
                query="How many days of annual leave do ACME employees receive?",
                context=TEST_CONTEXT,
                model="gpt-4.1-mini",
            )

        self.assertEqual(mock_parse.await_count, 2)


if __name__ == "__main__":
    unittest.main()
