from pathlib import Path

from src.ingestion.pdf_parser import parse_pdf


project_root = Path(__file__).resolve().parents[1]
pdf_path = project_root / "data" / "employee-handbook.pdf"

pages = parse_pdf(pdf_path)

for page in pages:
    print(f"\n--- Page {page['page']} ---")
    print(page["text"][:500])