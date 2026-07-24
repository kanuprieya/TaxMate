"""
One-off patch: relabel stale applicable_ay="AY2024-25" -> "AY2026-27"
========================================================================
The scraper/pdf_ingester defaults used to tag everything AY2024-25 before
the project settled on AY2026-27 as its reference AY. Source text is
unchanged (still today's CBDT rules) — only the AY label was wrong.
Re-running the scraper/embedder isn't needed; this just corrects the
metadata already on disk, mirroring patch_meta.py's approach.

Run:
    python patch_ay_labels.py
"""
import json
from pathlib import Path

CHUNKS_DIR = Path(__file__).parent / "rag_output" / "chunks"
COMBINED   = Path(__file__).parent / "rag_output" / "combined" / "all_chunks.jsonl"
META       = Path(__file__).parent / "vector_store" / "AY2026-27.meta.json"

OLD_AY, NEW_AY = "AY2024-25", "AY2026-27"


def patch_chunk_files():
    patched_files = 0
    for path in CHUNKS_DIR.glob("*_chunks.json"):
        with open(path, encoding="utf-8") as f:
            chunks = json.load(f)
        changed = False
        for c in chunks:
            if c.get("applicable_ay") == OLD_AY:
                c["applicable_ay"] = NEW_AY
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(chunks, f, ensure_ascii=False, indent=2)
            patched_files += 1
    print(f"Patched {patched_files} chunk files in {CHUNKS_DIR}")


def patch_combined():
    if not COMBINED.exists():
        print(f"Combined file not found: {COMBINED}")
        return
    lines = []
    changed = 0
    with open(COMBINED, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c.get("applicable_ay") == OLD_AY:
                c["applicable_ay"] = NEW_AY
                changed += 1
            lines.append(json.dumps(c, ensure_ascii=False))
    with open(COMBINED, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Patched {changed} entries in {COMBINED}")


def patch_meta():
    if not META.exists():
        print(f"Meta file not found: {META}")
        return
    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    changed = 0
    for chunk in meta.values():
        if chunk.get("applicable_ay") == OLD_AY:
            chunk["applicable_ay"] = NEW_AY
            changed += 1
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print(f"Patched {changed} entries in {META}")


if __name__ == "__main__":
    patch_chunk_files()
    patch_combined()
    patch_meta()
    print(f"\nDone. AY2024-25.faiss/.meta.json left untouched and unreferenced by any")
    print(f"default now — AY2026-27 is the one active, correctly-labeled namespace.")
