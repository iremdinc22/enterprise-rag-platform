import redis


redis_client = redis.Redis(
    host="localhost",
    port=6381,
    db=1,
    decode_responses=True
)


def create_job(job_id, document_id):
    redis_client.hset(
        f"job:{job_id}",
        mapping={
            "job_id": job_id,
            "document_id": document_id,
            "status": "queued",
            "error": ""
        }
    )


def update_job_status(job_id, status, error=""):
    redis_client.hset(
        f"job:{job_id}",
        mapping={
            "status": status,
            "error": error
        }
    )


def get_job(job_id):
    return redis_client.hgetall(
        f"job:{job_id}"
    )