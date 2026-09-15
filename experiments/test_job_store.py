from src.jobs.job_store import (
    create_job,
    update_job_status,
    get_job
)


job_id = "test-job-001"

create_job(
    job_id=job_id,
    document_id="employee-handbook"
)

print(get_job(job_id))

update_job_status(
    job_id,
    "processing"
)

print(get_job(job_id))

update_job_status(
    job_id,
    "completed"
)

print(get_job(job_id))