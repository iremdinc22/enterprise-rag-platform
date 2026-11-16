"""Test-only provider adapters and fail-closed network policy, installed pre-import."""
import hashlib
import os
from pathlib import Path
import re
import secrets
import socket
import sys
from types import ModuleType, SimpleNamespace

INSTALLED = False
STATE = SimpleNamespace(mode="normal", inputs=[], embedding_calls=0, blocked=0)


def install():
    global INSTALLED
    expected = {
        "E2E_ISOLATED": "1", "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_COLLECTION": "e2e_task028", "REDIS_CACHE_URL": "redis://redis:6379/2",
        "REDIS_JOBS_URL": "redis://redis:6379/1", "CELERY_BROKER_URL": "redis://redis:6379/0",
        "OPENAI_API_KEY": "e2e-not-an-api-key",
    }
    if any(os.environ.get(k) != v for k, v in expected.items()):
        raise RuntimeError("E2E requires its dedicated Compose configuration")
    os.environ["JWT_SECRET_KEY"] = secrets.token_hex(32)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    original_dns = socket.getaddrinfo
    allowed = {"127.0.0.1", "::1"}
    for host in ("qdrant", "redis"):
        allowed.update(info[4][0] for info in original_dns(host, None, type=socket.SOCK_STREAM))
    original_connect, original_ex = socket.socket.connect, socket.socket.connect_ex

    def permit(host):
        host = host.decode() if isinstance(host, bytes) else host
        if host in ("qdrant", "redis", "localhost") or host in allowed:
            return
        STATE.blocked += 1
        raise PermissionError("E2E network policy blocked an external destination")

    def dns(host, *args, **kwargs):
        permit(host)
        return original_dns(host, *args, **kwargs)

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            permit(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            permit(address[0])
        return original_ex(sock, address)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = dns, connect, connect_ex
    import openai
    openai.AsyncOpenAI = DeterministicOpenAI

    def blocked_sdk(*args, **kwargs):
        raise AssertionError("Real provider client is forbidden in E2E")

    openai.OpenAI = blocked_sdk
    module = ModuleType("sentence_transformers")
    module.CrossEncoder = ControlledCrossEncoder
    sys.modules["sentence_transformers"] = module
    INSTALLED = True


def embedding(text):
    vector = [0.0] * 1536
    for token in re.findall(r"\w+", text.lower()):
        position = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % 1536
        vector[position] += 1.0
    if not any(vector):
        vector[0] = 1.0
    return vector


class DeterministicOpenAI:
    def __init__(self, **kwargs):
        self.embeddings = SimpleNamespace(create=self.embed)
        self.responses = SimpleNamespace(parse=self.generate)

    async def embed(self, *, input, **kwargs):
        STATE.embedding_calls += 1
        from celery import current_task
        # A single controlled transient failure; Celery's real retry/message path handles it.
        if (current_task and len(current_task.request.args or []) > 1 and
                current_task.request.args[1] == "retry-e2e-doc" and current_task.request.retries == 0):
            import httpx
            raise httpx.ConnectError("E2E controlled transient embedding failure")
        texts = [input] if isinstance(input, str) else input
        return SimpleNamespace(data=[SimpleNamespace(embedding=embedding(t)) for t in texts],
                               usage=SimpleNamespace(prompt_tokens=10, total_tokens=10))

    async def generate(self, *, input, text_format, **kwargs):
        STATE.inputs.append(input)
        if STATE.mode == "generation_error":
            raise RuntimeError("SENSITIVE_INTERNAL_PROVIDER_DETAIL")
        source = re.search(r"\[SOURCE (\d+)\].*?Content:\n([^\[]+)", input, re.S)
        if source is None:
            raise AssertionError("Expected selected citation context")
        citation_id = int(source[1])
        content = source[2].strip().split("\n\n")[0]
        retrying = "Your previous response failed citation validation" in input
        invalid = STATE.mode == "invalid_citations" or (STATE.mode == "retry" and not retrying)
        answer = content + (" [999]" if invalid else f" [{citation_id}]")
        return SimpleNamespace(
            output_parsed=text_format(answer=answer, answered=True,
                                      citation_ids=[999 if invalid else citation_id]),
            usage=SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30,
                                  input_tokens_details=SimpleNamespace(cached_tokens=0)),
        )


class ControlledCrossEncoder:
    def __init__(self, *args, **kwargs):
        pass

    def predict(self, pairs):
        return [len(set(q.lower().split()) & set(t.lower().split())) +
                (10 if "allowance" in t.lower() else 0) for q, t in pairs]


def documents():
    """Small valid PDFs, no PDF-generation dependency or checked-in binary fixtures."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    root = Path("/tmp/task028-e2e-pdfs")
    root.mkdir(exist_ok=True)
    for tenant, amount in (("acme", "500"), ("globex", "2000")):
        writer = PdfWriter()
        texts = [f"{tenant.upper()} employees receive an annual allowance of {amount} USD. " +
                 " ".join(f"policy{i}" for i in range(60)),
                 f"{tenant.upper()} security requires multifactor authentication. " +
                 " ".join(f"security{i}" for i in range(45))]
        for text in texts:
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                    NameObject("/Subtype"): NameObject("/Type1"),
                                    NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject()
            words = text.split()
            lines = [" ".join(words[i:i+10]) for i in range(0, len(words), 10)]
            stream.set_data(("BT /F1 10 Tf 30 750 Td " + " 0 -14 Td ".join(f"({s}) Tj" for s in lines) + " ET").encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(root / f"{tenant}-e2e.pdf")
    return root
