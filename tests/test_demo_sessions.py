"""Memory-only authentication tests; no services, providers or runtime credentials."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "session-tests-bootstrap-only-0000000000000000")

import jwt
from fastapi.testclient import TestClient
from src.api.dependencies import get_pipeline
from src.api.main import create_app
from src.auth import jwt_handler
from src.demo import config
from src.demo.api import build_app
from src.demo.passwords import DemoCredentials, hash_password, validate_password_hash, verify_password
from src.demo.sessions import LoginAdmission, SessionProtection, SESSION_PATH
from src.api.resource_limits import QueryBodyLimit, QueryLimits
from tests.test_demo_infrastructure import demo_env

DEMO_KEY = "session-tests-demo-key-only-000000000000000000"
OTHER_KEY = "session-tests-other-environment-000000000000000"


class PasswordTests(unittest.TestCase):
    def test_real_scrypt_round_trip_salt_and_wrong_password(self):
        first = hash_password("Test fixture password 123!")
        second = hash_password("Test fixture password 123!")
        self.assertNotEqual(first, second)
        self.assertEqual(validate_password_hash(first), first)
        self.assertTrue(verify_password("Test fixture password 123!", first))
        self.assertFalse(verify_password("Wrong fixture password", first))
        self.assertNotIn("Test fixture", first)
        self.assertNotIn(first, repr(DemoCredentials(first, second)))

    def test_hash_setup_refuses_password_echo_fallback(self):
        import getpass
        from src.demo.passwords import main
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / ".env.demo.auth")
            with patch("sys.argv", ["setup", "--output", path]), patch("sys.stdin.isatty", return_value=True), \
                 patch("getpass.getpass", side_effect=getpass.GetPassWarning):
                with self.assertRaises(SystemExit): main()
            self.assertFalse(Path(path).exists())

    def test_hash_setup_refuses_noninteractive_input(self):
        from src.demo.passwords import main
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / ".env.demo.auth")
            with patch("sys.argv", ["setup", "--output", path]), patch("sys.stdin.isatty", return_value=False), \
                 patch("getpass.getpass") as prompt:
                with self.assertRaises(SystemExit): main()
            prompt.assert_not_called()
            self.assertFalse(Path(path).exists())

    def test_malformed_or_unbounded_hash_configuration_rejected(self):
        good = demo_env()["DEMO_ACME_PASSWORD_HASH"]
        for value in (None, "plaintext", "", good + "\n", good.replace("131072", "999999999"),
                      good.replace("$8$", "$16$"), good[:-1]):
            with self.subTest(kind=type(value).__name__), self.assertRaisesRegex(ValueError, '^Invalid demo password hash configuration$'):
                validate_password_hash(value)
        with self.assertRaises(ValueError): DemoCredentials.from_env({})

    def test_hash_setup_uses_private_exclusive_file_without_plaintext_or_output(self):
        from src.demo.passwords import main
        import io
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / ".env.demo.auth")
            with patch("sys.argv", ["setup", "--output", path]), \
                 patch("sys.stdin.isatty", return_value=True), \
                 patch("getpass.getpass", side_effect=["fixture-acme", "fixture-acme", "fixture-globex", "fixture-globex"]), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                main()
            text = Path(path).read_text()
            self.assertNotIn("fixture-", text + stdout.getvalue())
            self.assertNotIn("scrypt$", stdout.getvalue())
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            from dotenv import dotenv_values
            values = dotenv_values(path, interpolate=False)
            self.assertTrue(verify_password("fixture-acme", values["DEMO_ACME_PASSWORD_HASH"]))
            with patch("sys.argv", ["setup", "--output", path]), patch("getpass.getpass") as prompt:
                with self.assertRaises(SystemExit): main()
            prompt.assert_not_called()
            self.assertEqual(Path(path).read_text(), text)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        env = demo_env()
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))
        self.stack.enter_context(patch.object(config, "ACTIVE_SETTINGS", config.DemoSettings.from_env(env)))
        self.stack.enter_context(patch.object(jwt_handler, "SECRET_KEY", DEMO_KEY))
        self.now = 100.0
        self.app = build_app(login_clock=lambda: self.now)
        self.http = TestClient(self.app)
        self.addCleanup(self.http.close)
        self.verifier = self.stack.enter_context(patch("src.demo.sessions.verify_password", return_value=True))

    def login(self, username="acme-demo", password="fixture-password", **kwargs):
        return self.http.post(SESSION_PATH, json={"username": username, "password": password}, **kwargs)

    def assert_no_store(self, response):
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_success_fixed_accounts_claims_expiration_and_query_authorization(self):
        pipeline = AsyncMock(return_value={"answer": "Synthetic [1]", "citations": [{"citation_id": 1,
            "sources": [{"document_id": "policy", "filename": "fixture.pdf", "page": 1, "chunk_id": "chunk-001"}]}]})
        self.app.dependency_overrides[get_pipeline] = lambda: pipeline
        for username, tenant in (("acme-demo", "acme"), ("globex-demo", "globex")):
            response = self.login(username)
            self.assertEqual(response.status_code, 200)
            self.assert_no_store(response)
            body = response.json()
            self.assertEqual(set(body), {"access_token", "token_type", "expires_in", "user"})
            self.assertEqual(body["expires_in"], 900)
            self.assertEqual(body["token_type"], "bearer")
            self.assertEqual(body["user"], {"user_id": username, "tenant_id": tenant, "role": "admin"})
            claims = jwt.decode(body["access_token"], DEMO_KEY, algorithms=["HS256"])
            self.assertEqual(claims["exp"] - claims["iat"], 900)
            self.assertEqual((claims["tenant_id"], claims["role"], claims["sub"]), (tenant, "admin", username))
            with self.assertRaises(jwt.InvalidTokenError):
                jwt.decode(body["access_token"], OTHER_KEY, algorithms=["HS256"])
            normal = TestClient(create_app())
            self.addCleanup(normal.close)
            with patch.object(jwt_handler, "SECRET_KEY", OTHER_KEY):
                rejected = normal.post("/api/v1/query", json={"query": "Synthetic"},
                    headers={"Authorization": "Bearer " + body["access_token"]})
                self.assertEqual(rejected.status_code, 401)
            result = self.http.post("/api/v1/query?tenant_id=other", json={"query": "Synthetic"},
                headers={"Authorization": "Bearer " + body["access_token"], "X-Tenant-ID": "other"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(pipeline.await_args.kwargs["user_context"].tenant_id, tenant)
        self.assertEqual(self.app.state.login_admission.busy, False)

    def test_real_password_hash_login(self):
        encoded = hash_password("Actual fixture password")
        with patch("src.demo.sessions.DemoCredentials.from_env", return_value=DemoCredentials(encoded, encoded)):
            app = build_app()
        with patch("src.demo.sessions.verify_password", wraps=verify_password):
            http = TestClient(app)
            self.addCleanup(http.close)
            response = http.post(SESSION_PATH, json={"username": "acme-demo", "password": "Actual fixture password"})
            self.assertEqual(response.status_code, 200)
            response = http.post(SESSION_PATH, json={"username": "globex-demo", "password": "Incorrect fixture password"})
            self.assertEqual(response.status_code, 401)
            self.assert_no_store(response)

    def test_unknown_user_and_wrong_password_have_same_generic_401(self):
        self.verifier.return_value = False
        for username in ("acme-demo", "globex-demo", "unknown", " ACME-DEMO"):
            response = self.login(username)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "Invalid credentials"})
            self.assert_no_store(response)
        self.verifier.return_value = True
        self.assertEqual(self.login("unknown").status_code, 401)
        self.assertEqual(self.verifier.call_count, 5)  # Unknown accounts still do one hash.

    def test_extra_claims_types_and_query_parameters_rejected(self):
        payloads = [{"username": "acme-demo", "password": "fixture", field: value} for field, value in
                    (("tenant_id", "globex"), ("role", "owner"), ("sub", "attacker"), ("exp", 999), ("access_token", "fake"))]
        payloads += [{}, {"username": 1, "password": "fixture"}, {"username": "acme-demo", "password": 1},
                     {"username": "a" * 65, "password": "fixture"}]
        payloads += [{"username": "acme-demo", "password": value} for value in ("", "a" * 257, None, True, [], "\ud800")]
        for body in payloads:
            self.now += 60
            result = self.http.post(SESSION_PATH, content=json.dumps(body), headers={"Content-Type": "application/json"})
            self.assertEqual(result.status_code, 422)
            self.assert_no_store(result)
            self.assertEqual(result.json(), {"detail": "Invalid request"})
        self.now += 60
        self.assertEqual(self.login(params={"tenant_id": "globex"}).status_code, 422)
        self.verifier.assert_not_called()

    def test_expired_forged_and_other_environment_tokens_rejected_before_pipeline(self):
        pipeline = AsyncMock()
        self.app.dependency_overrides[get_pipeline] = lambda: pipeline
        good = jwt.decode(self.login().json()["access_token"], DEMO_KEY, algorithms=["HS256"])
        for encoded in (jwt.encode({**good, "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())}, DEMO_KEY, algorithm="HS256"),
                        jwt.encode(good, OTHER_KEY, algorithm="HS256"), "forged-token"):
            response = self.http.post("/api/v1/query", json={"query": "Synthetic"}, headers={"Authorization": "Bearer " + encoded})
            self.assertEqual(response.status_code, 401)
        pipeline.assert_not_called()

    def test_ten_attempts_then_429_and_exact_window_expiry(self):
        self.verifier.return_value = False
        for _ in range(10): self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.verifier.call_count, 10)
        blocked = self.login()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["retry-after"], "60")
        self.assert_no_store(blocked)
        self.assertEqual(self.verifier.call_count, 10)
        self.now += 60
        self.assertEqual(self.login().status_code, 401)

    def test_validation_attempts_count_but_health_does_not(self):
        for _ in range(10): self.assertEqual(self.http.post(SESSION_PATH, json={}).status_code, 422)
        self.assertEqual(self.login().status_code, 429)
        for _ in range(12): self.assertEqual(self.http.get("/health").status_code, 200)
        self.verifier.assert_not_called()
        self.assertEqual(self.app.state.query_admission.accepted, 0)

    def test_disabled_provider_and_missing_token_contract_unchanged(self):
        token = self.login().json()["access_token"]
        self.assertEqual(self.http.post("/api/v1/query", json={"query": "Synthetic"}).status_code, 401)
        response = self.http.post("/api/v1/query", json={"query": "Synthetic"}, headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Demo provider is disabled"})
        self.assertEqual(self.app.state.query_admission.accepted, 0)

    def test_missing_or_invalid_hashes_fail_before_serving(self):
        for key in ("DEMO_ACME_PASSWORD_HASH", "DEMO_GLOBEX_PASSWORD_HASH"):
            with patch.dict(os.environ, {key: "plaintext-not-a-hash"}), self.assertRaises(ValueError): build_app()

    def test_demo_disabled_or_inactive_cannot_build_and_normal_api_has_no_session(self):
        with patch.dict(os.environ, {"DEMO_ENABLED": "0"}), self.assertRaises(ValueError): build_app()
        with patch.object(config, "ACTIVE_SETTINGS", None), self.assertRaises(ValueError): build_app()
        normal = TestClient(create_app())
        self.addCleanup(normal.close)
        self.assertEqual(normal.post(SESSION_PATH, json={"username": "acme-demo", "password": "fixture"}).status_code, 404)

    def test_request_logging_contains_no_credentials_or_tokens(self):
        with self.assertLogs(level="INFO") as logs:
            response = self.login(password="private-fixture-password")
        token = response.json()["access_token"]
        output = "\n".join(logs.output)
        for value in ("private-fixture-password", token, DEMO_KEY, demo_env()["DEMO_ACME_PASSWORD_HASH"]):
            self.assertNotIn(value, output)

    def test_no_store_on_redirects_and_method_errors(self):
        self.assert_no_store(self.http.get(SESSION_PATH))
        result = self.http.post(SESSION_PATH + "/", json={}, follow_redirects=False)
        self.assertEqual(result.status_code, 307)
        self.assert_no_store(result)

    def test_unexpected_auth_error_is_sanitized_and_releases_hash_slot(self):
        self.verifier.side_effect = RuntimeError("PRIVATE-CREDENTIAL-CONTENT")
        result = self.login()
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("PRIVATE", result.text)
        self.assert_no_store(result)
        self.assertFalse(self.app.state.login_admission.busy)


class SessionBodyTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, messages=(), headers=(), receive=None, timeout=0.01):
        from src.api.resource_limits import QueryBodyLimit, QueryLimits
        from src.demo.sessions import SessionProtection, LoginAdmission, SESSION_PATH
        output, calls = [], []
        async def downstream(scope, recv, send):
            calls.append(await recv())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        iterator = iter(messages)
        async def recv(): return next(iterator)
        async def send(message): output.append(message)
        body = QueryBodyLimit(downstream, limits=QueryLimits(max_body_bytes=4096, body_timeout_seconds=timeout), path=SESSION_PATH)
        protected = SessionProtection(body, admission=LoginAdmission(lambda: 0))
        await protected({"type": "http", "method": "POST", "path": SESSION_PATH, "headers": list(headers)}, receive or recv, send)
        if output:
            self.assertIn((b"cache-control", b"no-store"), output[0]["headers"])
        return calls, output

    async def test_oversized_length_rejected_without_body_reception(self):
        receive = AsyncMock(side_effect=AssertionError("Do not read"))
        calls, output = await self.exercise(headers=[(b"content-length", b"4097")], receive=receive)
        self.assertEqual(output[0]["status"], 413)
        self.assertFalse(calls); receive.assert_not_awaited()

    async def test_missing_length_chunked_and_underdeclared_bytes(self):
        for headers in ([], [(b"transfer-encoding", b"chunked")], [(b"content-length", b"1")]):
            calls, output = await self.exercise([{"type": "http.request", "body": b"a" * 4096, "more_body": True},
                {"type": "http.request", "body": b"b"}], headers=headers)
            self.assertEqual(output[0]["status"], 413); self.assertFalse(calls)

    async def test_exact_body_boundary(self):
        calls, output = await self.exercise([{"type": "http.request", "body": b"a" * 4096}])
        self.assertEqual(output[0]["status"], 200)
        self.assertEqual(len(calls[0]["body"]), 4096)

    async def test_receive_timeout_and_disconnect(self):
        async def slow(): await asyncio.sleep(1)
        calls, output = await self.exercise(receive=slow)
        self.assertEqual(output[0]["status"], 408); self.assertFalse(calls)
        calls, output = await self.exercise([{"type": "http.disconnect"}])
        self.assertFalse(calls); self.assertFalse(output)

    async def test_malformed_framing_is_safe(self):
        calls, output = await self.exercise(headers=[(b"content-length", b"invalid")])
        self.assertEqual(output[0]["status"], 400); self.assertFalse(calls)


class LoginConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_hash_retains_slot_and_health_remains_responsive(self):
        import httpx
        from threading import Event
        from src.demo.api import build_app
        from src.demo import config
        from src.auth import jwt_handler
        from tests.test_demo_infrastructure import demo_env
        entered, release = Event(), Event()
        env = demo_env()
        def slow_verify(*args):
            entered.set()
            release.wait(5)
            return True
        with patch.dict(os.environ, env, clear=True), \
             patch.object(config, "ACTIVE_SETTINGS", config.DemoSettings.from_env(env)), \
             patch.object(jwt_handler, "SECRET_KEY", env["DEMO_JWT_SECRET"]), \
             patch("src.demo.sessions.verify_password", side_effect=slow_verify):
            app = build_app()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as http:
                pending = asyncio.create_task(http.post(SESSION_PATH, json={"username": "acme-demo", "password": "fixture"}))
                try:
                    while not entered.is_set(): await asyncio.sleep(0.001)
                    self.assertEqual((await http.get("/health")).status_code, 200)
                    pending.cancel()
                    await asyncio.sleep(0)
                    self.assertTrue(app.state.login_admission.busy)
                    rejected = await http.post(SESSION_PATH, json={"username": "globex-demo", "password": "fixture"})
                    self.assertEqual(rejected.status_code, 429)
                    self.assertEqual(rejected.headers["cache-control"], "no-store")
                    self.assertFalse(pending.done())
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError): await pending
                self.assertFalse(app.state.login_admission.busy)
                self.assertEqual((await http.post(SESSION_PATH, json={"username": "globex-demo", "password": "fixture"})).status_code, 200)

    def test_rate_admission_is_atomic_across_threads(self):
        from concurrent.futures import ThreadPoolExecutor
        from src.demo.sessions import LoginAdmission
        admission = LoginAdmission(lambda: 0)
        with ThreadPoolExecutor(20) as pool:
            outcomes = list(pool.map(lambda _: admission.attempt(), range(40)))
        self.assertEqual(outcomes.count(None), 10)
        self.assertEqual(len(admission.events), 10)
