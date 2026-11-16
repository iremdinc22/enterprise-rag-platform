from tests.e2e.bootstrap import install, documents

install()
documents()

from src.jobs import job_store

original_update = job_store.update_job_status


def tracked_update(job_id, status, error=""):
    # Observe transitions across the worker boundary while retaining the real hash write.
    original_update(job_id, status, error)
    job_store.redis_client.rpush(f"e2e:history:{job_id}", status)


job_store.update_job_status = tracked_update
from src.workers.celery_app import celery_app

# One fresh child per task avoids the existing process-global async clients being
# reused across separate asyncio.run loops. Production worker settings are unchanged.
celery_app.worker_main(["worker", "--concurrency=1", "--max-tasks-per-child=1",
                        "--pool=prefork", "--hostname=e2e@%h", "--loglevel=ERROR",
                        "--without-gossip", "--without-mingle"])
