"""
Quick patch: adds pdf_filename to existing FAISS metadata.
Uses compact JSON output (no indent) for speed on large files.
"""
import json, re
from pathlib import Path

META = Path(__file__).parent / "vector_store" / "AY2024-25.meta.json"
PDFS = Path(__file__).parent / "pdfs"

pdf_files = {f.stem.lower(): f.name for f in PDFS.glob("*.pdf")}
print(f"Found {len(pdf_files)} PDF files")

print("Loading metadata...")
with open(META, encoding="utf-8") as f:
    meta = json.load(f)
print(f"Loaded {len(meta)} chunks")

# Build sanitized stem -> filename lookup
stem_map = {}
for stem, filename in pdf_files.items():
    sanitized = re.sub(r"[^\w]", "_", stem)[:40]
    stem_map[sanitized] = filename

patched = 0
for key, chunk in meta.items():
    cid = chunk.get("chunk_id", "")
    if chunk.get("pdf_filename"):
        continue
    for san_stem, filename in stem_map.items():
        if cid.startswith(san_stem):
            chunk["pdf_filename"] = filename
            patched += 1
            break

print(f"Patched {patched} chunks")
print("Saving (compact)...")
with open(META, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)  # no indent = much faster
print(f"Done -> {META}")
