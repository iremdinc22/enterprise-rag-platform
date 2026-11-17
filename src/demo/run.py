"""Validate before importing existing clients. No implicit startup or ingestion."""
import argparse
import asyncio
import os
import sys

from src.demo.config import DemoSettings
from src.demo import config


def activate():
    settings = DemoSettings.from_env()
    # An already imported client/config could retain a host endpoint or secret.
    modules = ("src.config", "src.auth.jwt_handler", "src.api.main", "src.rag.pipeline",
               "src.ingestion.embedder", "src.generation.generator",
               "src.vector_store.qdrant_store", "src.workers.celery_app")
    if any(name in sys.modules for name in modules):
        raise ValueError("Invalid demo import order")
    from src.demo.providers import install_disabled_provider
    install_disabled_provider()
    os.environ["JWT_SECRET_KEY"] = settings.jwt_secret
    config.ACTIVE_SETTINGS = settings
    if settings.provider_mode == "deterministic":
        from src.demo import deterministic
        os.environ["EMBEDDING_MODEL"] = deterministic.EMBEDDING_MODEL
        os.environ["GENERATION_MODEL"] = deterministic.GENERATION_MODEL
        deterministic.install()
    return settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check-config", "api", "worker", "worker-ping", "init-collection"))
    args = parser.parse_args()
    try:
        if args.action == "check-config":
            settings = DemoSettings.from_env()
            print("Demo configuration valid; provider " + settings.provider_mode)
            return
        settings = activate()
    except ValueError:
        raise SystemExit("Invalid demo configuration") from None
    if args.action == "api":
        import uvicorn
        from src.demo.api import build_app
        uvicorn.run(build_app(), host="0.0.0.0", port=8000, access_log=False)
    elif args.action == "init-collection":
        from src.vector_store.init_collection import main as initialize
        asyncio.run(initialize())
    else:
        from src.workers.celery_app import celery_app
        if args.action == "worker-ping":
            replies = celery_app.control.ping(destination=["demo@" + os.environ["HOSTNAME"]], timeout=3)
            if not replies:
                raise SystemExit(1)
        else:
            from src.demo.ingestion import register_worker
            register_worker(celery_app, settings)
            celery_app.worker_main(["worker", "--loglevel=warning", "--concurrency=1", "--hostname=demo@%h",
                                   "--queues=demo.ingestion", "--max-tasks-per-child=1"])


if __name__ == "__main__":
    main()
