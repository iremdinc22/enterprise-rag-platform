"""No live operation without the explicit, separately approved command-line switch."""
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-approved-live-smoke", action="store_true")
    args = parser.parse_args()
    if not args.execute_approved_live_smoke:
        print("Live execution disabled. Explicit approval is required before using the execution switch.")
        return 0
    return execute()


def execute():
    # This function is never called by import/default CLI/network-free tests.
    import asyncio
    from contextlib import ExitStack
    import json
    import logging
    import os
    import socket
    import threading
    import time
    from unittest.mock import patch
    import httpx
    import httpx2
    import openai
    import uvicorn
    from tests.live_smoke.guard import Ledger, GuardedTransport, GuardStop
    from tests.live_smoke.runtime import (BODY, ScopedCache, ScopedRedis, ReadOnlyQdrant, configured, network_policy, sdk_client, validate_answer)

    ledger = Ledger()
    server = thread = loop = client = cache = redis = qdrant = None
    statuses = []
    evidence = []
    try:
        configured()  # Refuse models/endpoints outside the audited local setup.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        logging.disable(logging.CRITICAL)  # Dedicated process: suppress descendant SDK/HTTP loggers too.
        for name in ("httpx", "httpx2", "openai", "httpcore", "httpcore2"):
            logging.getLogger(name).disabled = True
        with ExitStack() as stack:
            sending = stack.enter_context(network_policy())
            client = sdk_client(ledger, GuardedTransport(ledger, httpx2.AsyncHTTPTransport(retries=0), sending))
            # Source clients request their usual defaults, but every actual client is this guarded one.
            stack.enter_context(patch.object(openai, "AsyncOpenAI", lambda **kwargs: client))
            def blocked(*args, **kwargs):
                ledger.stop()
            for name in ("OpenAI", "AzureOpenAI", "AsyncAzureOpenAI"):
                stack.enter_context(patch.object(openai, name, blocked))
            from src.vector_store import qdrant_store
            qdrant = qdrant_store.client
            stack.enter_context(patch.object(qdrant_store, "client", ReadOnlyQdrant(qdrant, ledger)))
            from src.api import main as api_main
            from src.rag import pipeline
            from src.cache import result_cache, embedding_cache
            from src.auth.jwt_handler import create_access_token
            cache = ScopedCache(ledger)
            redis = ScopedRedis(result_cache.redis_client, cache)
            stack.enter_context(patch.object(result_cache, "redis_client", redis))
            stack.enter_context(patch.object(embedding_cache, "redis_client", ScopedRedis(embedding_cache.redis_client, cache)))
            stack.enter_context(patch.object(result_cache, "build_result_cache_key", cache.key(result_cache.build_result_cache_key)))
            stack.enter_context(patch.object(embedding_cache, "build_embedding_cache_key", cache.key(embedding_cache.build_embedding_cache_key)))
            stack.enter_context(patch.object(pipeline, "get_cached_result", cache.lookup(pipeline.get_cached_result)))
            stack.enter_context(patch.object(api_main, "configure_tracing", lambda: False))
            # Prevent arbitrary local callers from sharing this small test server.
            tokens = {t: create_access_token("controlled-live-smoke", t, "employee", expires_minutes=5) for t in ("acme", "globex")}
            app = api_main.create_app()
            seen = [0]
            @app.middleware("http")
            async def predefined_only(request, call_next):
                if (request.method != "POST" or request.url.path != "/api/v1/query" or request.url.query or
                        ledger.finished or ledger.aborted or ledger.phase not in (1, 2, 3) or ledger.phase != seen[0] + 1 or
                        request.headers.get("Authorization") != "Bearer " + tokens[("acme", "globex", "acme")[ledger.phase - 1]] or
                        await request.body() != json.dumps(BODY).encode()):
                    ledger.stop()
                seen[0] += 1
                return await call_next(request)
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            loop = asyncio.new_event_loop()
            server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
            def serve():
                asyncio.set_event_loop(loop)
                loop.run_until_complete(server.serve(sockets=[sock]))
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            deadline = time.monotonic() + 90
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    ledger.stop()
                time.sleep(0.05)
            first = None
            with httpx.Client(timeout=90, trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
                for index, tenant in enumerate(("acme", "globex", "acme"), 1):
                    if index == 3:
                        key = result_cache.build_result_cache_key("acme", BODY["query"], "gpt-4.1-mini", 5, 3, 0.5)
                        cache.pin(redis, key, first)
                    ledger.begin(index, tenant)
                    response = http.post(f"http://127.0.0.1:{port}/api/v1/query",
                                         content=json.dumps(BODY).encode(),
                                         headers={"Authorization": "Bearer " + tokens[tenant], "Content-Type": "application/json"})
                    statuses.append(response.status_code)
                    if response.status_code != 200 or ledger.aborted:
                        ledger.stop()
                    value = validate_answer(response.json(), tenant)
                    evidence.append({"tenant": tenant, "citations": value["citations"]})
                    if index == 1:
                        first = value
                    if index == 3 and value != first:
                        ledger.stop()
                    ledger.finish()
            # No bodies, token strings, prompts, or provider error messages in the report.
            print(json.dumps({**ledger.summary(), "statuses": statuses, "cache": ["MISS", "MISS", "PINNED_HIT"], "evidence": evidence}))
            return 0
    except BaseException:
        ledger.aborted = True
        print(json_summary(ledger, statuses))
        return 1
    finally:
        if loop and thread and thread.is_alive():
            async def close():
                if client:
                    await client.close()
                if qdrant is not None:
                    await qdrant.close()
            try:
                asyncio.run_coroutine_threadsafe(close(), loop).result(10)
            except Exception:
                pass
            server.should_exit = True
            thread.join(10)
        if cache and redis:
            try:
                cache.cleanup(redis)
            except Exception:
                pass  # Do not delete anything except exact namespaced keys, even on failure.


def json_summary(ledger, statuses):
    import json
    return json.dumps({**ledger.summary(), "statuses": statuses, "result": "STOPPED"})


if __name__ == "__main__":
    raise SystemExit(main())
