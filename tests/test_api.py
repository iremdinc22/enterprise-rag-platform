import os
import unittest
from unittest.mock import AsyncMock, patch

# Explicit test configuration; never use repository credentials in API tests.
os.environ["JWT_SECRET_KEY"] = "api-test-secret-only-000000000000000000000000"

import jwt
from fastapi.testclient import TestClient
from src.api.dependencies import get_pipeline
from src.api.main import create_app
from src.auth.jwt_handler import ALGORITHM, SECRET_KEY, create_access_token


class APITests(unittest.TestCase):
    def setUp(self):
        self.pipeline = AsyncMock(return_value={"answer": "Policy [1]", "citations": [
            {"citation_id": 1, "sources": [{"document_id": "policy", "filename": "p.pdf",
             "page": 1, "chunk_id": "c1", "text": "PRIVATE", "tenant_id": "acme"}]}]})
        self.app = create_app()
        self.app.dependency_overrides[get_pipeline] = lambda: self.pipeline
        self.client = TestClient(self.app)

    def headers(self, tenant="acme", **kwargs):
        return {"Authorization": "Bearer " + create_access_token("user", tenant, "employee", **kwargs)}

    def test_success_and_tenant_isolation(self):
        for tenant in ("acme", "globex"):
            response = self.client.post("/api/v1/query?tenant_id=attacker", json={"query": "Policy?"},
                                        headers={**self.headers(tenant), "X-Tenant-ID": "attacker"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.pipeline.call_args.kwargs["user_context"].tenant_id, tenant)
            self.assertEqual(self.pipeline.call_args.kwargs["model"], os.getenv("GENERATION_MODEL", "gpt-4.1-mini"))
            self.assertNotIn("PRIVATE", response.text)

    def test_invalid_tokens(self):
        good = jwt.decode(create_access_token("user", "acme", "employee"), SECRET_KEY, algorithms=[ALGORITHM])
        bad = []
        for claim in good:
            payload = good.copy()
            del payload[claim]
            bad.append(jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM))
        for field, value in (("tenant_id", " "), ("tenant_id", None), ("role", "owner"), ("sub", "")):
            bad.append(jwt.encode({**good, field: value}, SECRET_KEY, algorithm=ALGORITHM))
        bad.extend(["garbage", create_access_token("u", "acme", "employee", expires_minutes=-1),
                    jwt.encode(good, "wrong-secret-000000000000000000000000000", algorithm=ALGORITHM),
                    jwt.encode(good, "", algorithm="none")])
        for token in bad:
            with self.subTest(token_type=token[:8]):
                response = self.client.post("/api/v1/query", json={"query": "Q"}, headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
        for headers in ({}, {"Authorization": "Basic abc"}):
            self.assertEqual(self.client.post("/api/v1/query", json={"query": "Q"}, headers=headers).status_code, 401)
        self.pipeline.assert_not_called()

    def test_validation(self):
        for body in ({}, {"query": " "}, {"query": "x" * 8001}, {"query": 123},
                     {"query": "Q", "tenant_id": "secret-tenant"}, {"query": "Q", "model": "arbitrary"},
                     {"query": "Q", "retrieval_limit": 0}, {"query": "Q", "final_limit": 6},
                     {"query": "Q", "lambda_value": 2}, {"query": "Q", "use_cache": "false"}):
            response = self.client.post("/api/v1/query", json=body, headers=self.headers())
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("secret-tenant", response.text)
        self.pipeline.assert_not_called()
        response = self.client.post("/api/v1/query", content='{bad-secret', headers={**self.headers(), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("bad-secret", response.text)

    def test_pipeline_error_and_invalid_response(self):
        for value in (RuntimeError("PRIVATE credentials"), {"answer": 2}):
            self.pipeline.side_effect = value if isinstance(value, Exception) else None
            self.pipeline.return_value = value
            response = self.client.post("/api/v1/query", json={"query": "Q"}, headers=self.headers())
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "Query processing failed"})

    def test_probes(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        with patch("src.api.main.check_ready", new=AsyncMock()):
            self.assertEqual(self.client.get("/ready").status_code, 200)
        with patch("src.api.main.check_ready", new=AsyncMock(side_effect=RuntimeError("PRIVATE"))):
            response = self.client.get("/ready")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("PRIVATE", response.text)
