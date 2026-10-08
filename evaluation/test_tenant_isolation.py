import unittest

from src.retrieval.context_selector import select_context


class TestTenantIsolation(unittest.IsolatedAsyncioTestCase):

    async def test_acme_retrieval_isolation(self):
        contexts = await select_context(
            query="What is the remote work allowance?",
            tenant_id="acme",
            retrieval_limit=5,
            final_limit=3,
            lambda_value=0.5,
        )

        self.assertTrue(
            contexts,
            "ACME retrieval returned no contexts",
        )

        for context in contexts:
            self.assertEqual(
                context["tenant_id"],
                "acme",
                "Cross-tenant context returned for ACME",
            )

    async def test_globex_retrieval_isolation(self):
        contexts = await select_context(
            query="What is the remote work allowance?",
            tenant_id="globex",
            retrieval_limit=5,
            final_limit=3,
            lambda_value=0.5,
        )

        self.assertTrue(
            contexts,
            "GLOBEX retrieval returned no contexts",
        )

        for context in contexts:
            self.assertEqual(
                context["tenant_id"],
                "globex",
                "Cross-tenant context returned for GLOBEX",
            )

    async def test_acme_does_not_retrieve_globex_allowance(self):
        contexts = await select_context(
            query="What is the remote work allowance?",
            tenant_id="acme",
            retrieval_limit=5,
            final_limit=3,
            lambda_value=0.5,
        )

        combined_text = " ".join(
            context["text"] for context in contexts
        )

        self.assertIn("500 USD", combined_text)
        self.assertNotIn("2000 USD", combined_text)

    async def test_globex_does_not_retrieve_acme_allowance(self):
        contexts = await select_context(
            query="What is the remote work allowance?",
            tenant_id="globex",
            retrieval_limit=5,
            final_limit=3,
            lambda_value=0.5,
        )

        combined_text = " ".join(
            context["text"] for context in contexts
        )

        self.assertIn("2000 USD", combined_text)
        self.assertNotIn("500 USD", combined_text)


if __name__ == "__main__":
    unittest.main()
