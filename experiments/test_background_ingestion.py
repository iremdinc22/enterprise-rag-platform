from pathlib import Path

from src.workers.celery_app import ingest_document_task


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook.pdf"


result = ingest_document_task.delay(
    str(pdf_path),
    "employee-handbook"
)

print("Task sent to Celery")
print(f"Task ID: {result.id}")