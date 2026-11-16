"""Demo configuration and entrypoints: no Docker startup, sockets or providers."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from src.demo.config import DemoSettings
from src.demo.providers import DisabledOpenAI, ProviderDisabled

ROOT = Path(__file__).resolve().parents[1]


def demo_env():
    return {
        "DEMO_ENABLED": "1", "DEMO_PROVIDER_MODE": "disabled",
        "DEMO_JWT_SECRET": "test-only-demo-signing-secret-0000000000000000",
        "PYTHON_DOTENV_DISABLED": "1", "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_COLLECTION": "enterprise_demo_documents",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "REDIS_JOBS_URL": "redis://redis:6379/1", "REDIS_CACHE_URL": "redis://redis:6379/2",
        "DEMO_UPLOAD_ROOT": "/demo/uploads", "HF_HOME": "/home/app/.cache/huggingface",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "RAG_TRACING_ENABLED": "false",
        "DEMO_MAX_UPLOAD_BYTES": "5242880", "DEMO_MAX_PDF_PAGES": "10", "DEMO_MAX_CHUNKS": "200",
    }


class DemoConfigurationTests(unittest.TestCase):
    def test_explicit_settings_and_secret_redaction(self):
        settings = DemoSettings.from_env(demo_env())
        self.assertEqual(settings.max_upload_bytes, 5242880)
        self.assertEqual(settings.max_pdf_pages, 10)
        self.assertEqual(settings.max_chunks, 200)
        self.assertNotIn(settings.jwt_secret, repr(settings))

    def test_every_setting_is_required_without_host_fallback(self):
        for key in demo_env():
            env = demo_env(); env.pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '^Invalid demo configuration$'):
                DemoSettings.from_env(env)

    def test_unsafe_services_collection_paths_and_provider_modes_fail_closed(self):
        invalid = {
            "QDRANT_URL": ["http://localhost:6333", "http://127.0.0.1:6333", "http://qdrant:6333/", "http://host.docker.internal:6333"],
            "QDRANT_COLLECTION": ["enterprise_documents", "e2e_task028", "", "other"],
            "CELERY_BROKER_URL": ["redis://localhost:6381/0", "redis://redis:6379/1"],
            "REDIS_JOBS_URL": ["redis://redis:6379/0"], "REDIS_CACHE_URL": ["redis://redis:6379/1"],
            "DEMO_UPLOAD_ROOT": ["/app/data", "/demo/uploads/../data", "uploads"],
            "HF_HOME": ["/app/data"], "DEMO_PROVIDER_MODE": ["openai", "deterministic", ""],
            "DEMO_ENABLED": ["0"], "PYTHON_DOTENV_DISABLED": ["0"],
            "HF_HUB_OFFLINE": ["0"], "TRANSFORMERS_OFFLINE": ["0"], "RAG_TRACING_ENABLED": ["true"],
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    DemoSettings.from_env({**demo_env(), key: value})

    def test_existing_credentials_cannot_be_inherited(self):
        for key in ("JWT_SECRET_KEY", "OPENAI_API_KEY"):
            with self.assertRaises(ValueError):
                DemoSettings.from_env({**demo_env(), key: "host-credential-not-allowed"})

    def test_invalid_secrets_have_no_value_in_errors(self):
        for value in ("", "short", " " * 40, "x" * 257, "é" * 40, "a" * 32 + "\n", "a" * 32 + "\0"):
            with self.assertRaisesRegex(ValueError, '^Invalid demo configuration$'):
                DemoSettings.from_env({**demo_env(), "DEMO_JWT_SECRET": value})

    def test_limits_are_positive_and_cannot_exceed_approved_maxima(self):
        for key, maximum in (("DEMO_MAX_UPLOAD_BYTES", 5242880), ("DEMO_MAX_PDF_PAGES", 10), ("DEMO_MAX_CHUNKS", 200)):
            for value in ("0", "-1", str(maximum + 1), "1.0", "", "true"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    DemoSettings.from_env({**demo_env(), key: value})
            DemoSettings.from_env({**demo_env(), key: "1"})

    def test_invalid_config_exits_before_any_client_import(self):
        script = '''
import sys
from unittest.mock import patch
from src.demo.run import activate
with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
    try: activate()
    except ValueError: pass
    else: raise AssertionError("Expected failure")
assert "src.config" not in sys.modules
assert "src.workers.celery_app" not in sys.modules
assert "openai" not in sys.modules
'''
        env = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run([sys.executable, "-c", script], env=env, cwd=ROOT, check=True, timeout=30)

    def test_check_config_does_not_connect_or_load_clients(self):
        script = '''
import sys
from unittest.mock import patch
from src.demo.run import main
sys.argv = ["demo", "check-config"]
with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")): main()
assert "openai" not in sys.modules and "src.config" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", script], env={"PATH": os.environ["PATH"], **demo_env()}, cwd=ROOT,
                                capture_output=True, text=True, check=True, timeout=30)
        self.assertNotIn(demo_env()["DEMO_JWT_SECRET"], result.stdout + result.stderr)

    def test_import_order_is_rejected(self):
        from src.demo.run import activate
        with patch.dict(os.environ, demo_env(), clear=True), patch.dict(sys.modules, {"src.config": object()}):
            with self.assertRaises(ValueError): activate()

    def test_provider_clients_never_reach_transport(self):
        async def exercise():
            client = DisabledOpenAI()
            for method in (client.embeddings.create, client.responses.parse, client.responses.create):
                with self.assertRaisesRegex(ProviderDisabled, '^Demo provider is disabled$'):
                    await method(input="never logged")
            with self.assertRaises(ProviderDisabled):
                client.responses = client.unknown_resource
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            asyncio.run(exercise())

    def test_demo_api_auth_and_disabled_query_without_rag_import_or_network(self):
        script = '''
import sys
from unittest.mock import patch
from src.demo.run import activate
with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
    settings = activate()
    from src.demo.api import build_app
    from fastapi.testclient import TestClient
    from src.auth.jwt_handler import create_access_token
    with TestClient(build_app()) as http:
        assert http.get("/health").status_code == 200
        assert http.post("/api/v1/query", json={"query":"Synthetic"}).status_code == 401
        for tenant in ("acme", "globex"):
            token = create_access_token("demo", tenant, "admin")
            result = http.post("/api/v1/query", json={"query":"Synthetic"}, headers={"Authorization":"Bearer " + token})
            assert result.status_code == 503
            assert result.json() == {"detail":"Demo provider is disabled"}
        assert http.app.state.query_admission.accepted == 0
    assert "src.rag.pipeline" not in sys.modules
    assert "src.ingestion.embedder" not in sys.modules
'''
        subprocess.run([sys.executable, "-c", script], env={"PATH": os.environ["PATH"], **demo_env()}, cwd=ROOT,
                       check=True, timeout=30)

    def test_api_cannot_be_built_without_activation(self):
        script = '''
import sys
from src.demo.api import build_app
try: build_app()
except ValueError: pass
else: raise AssertionError("Expected activation guard")
assert "src.api.main" not in sys.modules
'''
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, timeout=30)

    def test_worker_import_uses_disabled_existing_embedding_client(self):
        script = '''
import asyncio
from unittest.mock import patch
from src.demo.run import activate
from src.demo.providers import ProviderDisabled, DisabledOpenAI
with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")), patch("qdrant_client.AsyncQdrantClient"):
    activate()
    from src.workers.celery_app import celery_app
    from src.ingestion import embedder
    assert isinstance(embedder.client, DisabledOpenAI)
    assert isinstance(embedder.query_client, DisabledOpenAI)
    assert celery_app.conf.broker_url == "redis://redis:6379/0"
    async def exercise():
        try: await embedder.create_embeddings(["Synthetic"])
        except ProviderDisabled: pass
        else: raise AssertionError("Provider must be disabled")
    asyncio.run(exercise())
'''
        subprocess.run([sys.executable, "-c", script], env={"PATH": os.environ["PATH"], **demo_env()}, cwd=ROOT,
                       check=True, timeout=30)


class DemoFrontendStartupTests(unittest.TestCase):
    def test_module_entrypoint_starts_placeholder_server(self):
        import runpy
        with patch("http.server.ThreadingHTTPServer") as server:
            runpy.run_module("src.demo.frontend", run_name="__main__")
        server.assert_called_once()
        self.assertEqual(server.call_args.args[0], ("0.0.0.0", 5173))
        server.return_value.serve_forever.assert_called_once_with()


class DemoComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker CLI required for static Compose parsing")
        command = ["docker", "compose", "--env-file", "/dev/null", "-f", "compose.demo.yaml", "config", "--format", "json"]
        result = subprocess.run(command, env={"PATH": os.environ["PATH"],
            "DEMO_JWT_SECRET": demo_env()["DEMO_JWT_SECRET"]}, cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError("Static demo Compose configuration invalid")
        cls.config = json.loads(result.stdout)

    def test_project_and_network_are_separate_and_internal(self):
        cfg = self.config
        self.assertEqual(cfg["name"], "enterprise-rag-demo")
        self.assertEqual(set(cfg["services"]), {"frontend", "api", "worker", "qdrant", "redis"})
        self.assertTrue(cfg["networks"]["demo"]["internal"])
        self.assertFalse(cfg["networks"]["demo"].get("external", False))
        self.assertFalse(cfg["networks"]["edge"].get("internal", False))
        self.assertFalse(cfg["networks"]["edge"].get("external", False))
        for name, service in cfg["services"].items():
            self.assertEqual(set(service["networks"]), {"demo", "edge"} if name == "frontend" else {"demo"})
            self.assertNotIn("container_name", service)
            self.assertNotIn("network_mode", service)

    def test_only_frontend_publishes_loopback_port(self):
        for name, service in self.config["services"].items():
            if name == "frontend":
                ports = service["ports"]
                self.assertEqual(len(ports), 1)
                self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
                self.assertEqual(str(ports[0]["published"]), "18030")
                self.assertEqual(ports[0]["target"], 5173)
            else:
                self.assertFalse(service.get("ports"))

    def test_volumes_are_project_scoped_and_upload_permissions_are_split(self):
        cfg = self.config
        self.assertEqual(set(cfg["volumes"]), {"uploads", "qdrant_data", "redis_data", "model_cache"})
        for name, volume in cfg["volumes"].items():
            self.assertEqual(volume["name"], "enterprise-rag-demo_" + name)
            self.assertFalse(volume.get("external", False))
        for service in cfg["services"].values():
            for volume in service.get("volumes", []):
                self.assertEqual(volume["type"], "volume")
                self.assertIn(volume["source"], cfg["volumes"])
        for service, readonly in (("api", False), ("worker", True)):
            upload = next(v for v in cfg["services"][service]["volumes"] if v["source"] == "uploads")
            self.assertEqual(upload["target"], "/demo/uploads")
            self.assertEqual(upload.get("read_only", False), readonly)

    def test_backend_env_validates_and_frontend_has_no_credentials(self):
        for name in ("api", "worker"):
            env = self.config["services"][name]["environment"]
            DemoSettings.from_env(env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("JWT_SECRET_KEY", env)
            self.assertFalse(self.config["services"][name].get("env_file"))
            self.assertEqual(self.config["services"][name]["command"][:3], ["python", "-m", "src.demo.run"])
        self.assertFalse(self.config["services"]["frontend"].get("environment"))
        self.assertFalse(self.config["services"]["frontend"].get("volumes"))

    def test_collection_is_not_implicitly_initialized(self):
        for service in self.config["services"].values():
            self.assertNotIn("init-collection", (service.get("command") or []))
