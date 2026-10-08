import unittest

from evaluation.retrieval_metrics import (
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    mean_reciprocal_rank,
)

from evaluation.retrieval_runner import (
    extract_retrieved_source_groups,
    calculate_context_metrics,
)


class TestRetrievalMetrics(unittest.TestCase):

    def test_precision_at_k(self):
        retrieved = ["C", "A", "D", "B", "E"]
        relevant = ["A", "B"]

        result = precision_at_k(
            retrieved_ids=retrieved,
            relevant_ids=relevant,
            k=5,
        )

        self.assertAlmostEqual(result, 0.4)

    def test_recall_at_k(self):
        retrieved = ["C", "A", "D", "B", "E"]
        relevant = ["A", "B"]

        result = recall_at_k(
            retrieved_ids=retrieved,
            relevant_ids=relevant,
            k=5,
        )

        self.assertAlmostEqual(result, 1.0)

    def test_reciprocal_rank(self):
        retrieved = ["C", "A", "D", "B", "E"]
        relevant = ["A", "B"]

        result = reciprocal_rank(
            retrieved_ids=retrieved,
            relevant_ids=relevant,
        )

        self.assertAlmostEqual(result, 0.5)

    def test_mean_reciprocal_rank(self):
        cases = [
            {
                "retrieved_ids": ["A", "C"],
                "relevant_ids": ["A"],
            },
            {
                "retrieved_ids": ["C", "B"],
                "relevant_ids": ["B"],
            },
            {
                "retrieved_ids": ["C", "D", "E", "A"],
                "relevant_ids": ["A"],
            },
        ]

        result = mean_reciprocal_rank(cases)

        self.assertAlmostEqual(result, 0.5833333333)

    def test_recall_without_relevant_documents(self):
        result = recall_at_k(
            retrieved_ids=["A", "B"],
            relevant_ids=[],
            k=5,
        )

        self.assertIsNone(result)

    def test_invalid_k(self):
        with self.assertRaises(ValueError):
            precision_at_k(
                retrieved_ids=["A"],
                relevant_ids=["A"],
                k=0,
            )

        with self.assertRaises(ValueError):
            recall_at_k(
                retrieved_ids=["A"],
                relevant_ids=["A"],
                k=0,
            )

    def test_multiple_sources_match_ground_truth(self):
        """
        A context can have multiple source IDs.

        The relevant source may not be the first one.
        The context should still count as relevant.
        """
        source_groups = [
            [
                "second-document:chunk-001",
                "employee-handbook:chunk-001",
            ],
            [
                "employee-handbook:chunk-002",
            ],
        ]

        relevant_ids = [
            "employee-handbook:chunk-001"
        ]

        precision, recall, rr = calculate_context_metrics(
            source_groups=source_groups,
            relevant_ids=relevant_ids,
            k=2,
        )

        self.assertAlmostEqual(precision, 0.5)
        self.assertAlmostEqual(recall, 1.0)
        self.assertAlmostEqual(rr, 1.0)

    def test_extract_multiple_source_ids(self):
        """
        All source IDs should be preserved.

        Multiple sources belonging to the same context
        must remain grouped together.
        """
        contexts = [
            {
                "text": (
                    "ACME employees receive "
                    "20 days of annual leave."
                ),
                "sources": [
                    {
                        "document_id": "second-document",
                        "chunk_id": "chunk-001",
                    },
                    {
                        "document_id": "employee-handbook",
                        "chunk_id": "chunk-001",
                    },
                ],
            }
        ]

        result = extract_retrieved_source_groups(contexts)

        expected = [
            [
                "second-document:chunk-001",
                "employee-handbook:chunk-001",
            ]
        ]

        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
