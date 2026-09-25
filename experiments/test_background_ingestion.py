from pathlib import Path
from uuid import uuid4

from src.jobs.job_store import create_job, get_job
from src.workers.celery_app import ingest_document_task


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook.pdf"

job_id = str(uuid4())

tenant_id = "acme"
document_id = "second-document"

create_job(
    job_id=job_id,
    document_id=document_id
)

ingest_document_task.apply_async(
    args=[
        str(pdf_path),
        document_id,
        tenant_id
    ],
    task_id=job_id
)

print("Task sent to Celery")
print(f"Job ID: {job_id}")
print(get_job(job_id))