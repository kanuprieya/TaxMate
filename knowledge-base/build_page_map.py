"""
Build a page map: chunk_id -> pdf_page_number
==============================================
Reads existing chunk JSON files and matches each chunk's text against
PDF pages to find which page it came from. Saves a lightweight JSON
that the RAG service loads at startup.

This avoids re-ingesting or re-embedding — just text matching.

Usage:
    python build_page_map.py
"""

import json
import re
from pathlib import Path

CHUNKS_DIR = Path(__file__).parent / "rag_output" / "chunks"
PDF_DIR    = Path(__file__).parent / "pdfs"
OUTPUT     = Path(__file__).parent / "vector_store" / "page_map.json"

# Same sanitization as pdf_ingester uses for file_id
def _sanitize_stem(stem: str) -> str:
    return re.sub(r"[^\w]", "_", stem.lower())[:40]

def _extract_pages(pdf_path: Path) -> dict[int, str]:
    """Extract text from each page. Returns {page_num: text}."""
    pages = {}
    try:
        import fitz  # PyMuPDF — faster
        doc = fitz.open(str(pdf_path))
        for i, page in enumerate(doc):
            text = page.get_text("text").strip().lower()
            if text:
                pages[i + 1] = text
        doc.close()
        return pages
    except ImportError:
        pass

    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip().lower()
            if text:
                pages[i + 1] = text
    return pages


def main():
    print("Building page map for existing chunks...\n")

    # Build sanitized stem -> (filename, pdf_path) mapping
    pdf_map: dict[str, tuple[str, Path]] = {}
    for f in PDF_DIR.glob("*.pdf"):
        san = _sanitize_stem(f.stem)
        pdf_map[san] = (f.name, f)
    print(f"Found {len(pdf_map)} PDF files\n")

    page_map: dict[str, int] = {}
    processed_pdfs = set()

    # Process each PDF chunk file
    for chunk_file in sorted(CHUNKS_DIR.glob("pdf_*_chunks.json")):
        print(f"Processing: {chunk_file.name}")
        with open(chunk_file, encoding="utf-8") as f:
            chunks = json.load(f)

        if not chunks:
            continue

        # Find the matching PDF by checking chunk_id prefix against sanitized stems
        matched_pdf = None
        matched_stem = None
        sample_id = chunks[0].get("chunk_id", "")
        for san_stem, (filename, pdf_path) in pdf_map.items():
            if sample_id.startswith(san_stem):
                matched_pdf = pdf_path
                matched_stem = san_stem
                break

        if not matched_pdf:
            print(f"  [!] Could not match to a PDF file, skipping")
            continue

        print(f"  Matched PDF: {matched_pdf.name}")

        # Extract pages from PDF (cached per PDF)
        if matched_pdf.name not in processed_pdfs:
            print(f"  Extracting pages...")
        pdf_pages = _extract_pages(matched_pdf)
        processed_pdfs.add(matched_pdf.name)
        print(f"  {len(pdf_pages)} pages extracted")

        # For each chunk, find the best matching page
        matched_count = 0
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id", "")
            chunk_text = chunk.get("text", "").lower()

            if not chunk_text or len(chunk_text) < 20:
                continue

            # Take a snippet from the middle of the chunk (less likely to be a heading)
            mid = len(chunk_text) // 2
            # Get a clean 60-char snippet avoiding word boundaries
            snippet_start = max(0, mid - 30)
            snippet = chunk_text[snippet_start:snippet_start + 60].strip()
            # Also try first 60 chars as fallback
            first_snippet = chunk_text[:80].strip()

            best_page = 0
            for page_num, page_text in pdf_pages.items():
                if snippet in page_text or first_snippet in page_text:
                    best_page = page_num
                    break

            if best_page > 0:
                page_map[chunk_id] = best_page
                matched_count += 1

        print(f"  > Mapped {matched_count}/{len(chunks)} chunks to pages\n")

    # Save
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(page_map, f)

    print(f"Done! Page map saved -> {OUTPUT}")
    print(f"   Total mappings: {len(page_map)}")


if __name__ == "__main__":
    main()
