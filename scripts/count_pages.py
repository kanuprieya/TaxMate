import os
import PyPDF2
from pathlib import Path

pdf_dir = Path(r"c:\Antigravity\itr1-rag-agent\knowledge-base\pdfs")
total_pages = 0

for pdf_file in pdf_dir.glob("*.pdf"):
    try:
        with open(pdf_file, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            pages = len(reader.pages)
            print(f"{pdf_file.name}: {pages} pages")
            total_pages += pages
    except Exception as e:
        print(f"Error reading {pdf_file.name}: {e}")

print(f"\nTotal Pages across all documents: {total_pages}")
