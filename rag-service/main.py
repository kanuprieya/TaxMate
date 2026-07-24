"""
RAG Service — FastAPI
======================
Loads the FAISS index built by knowledge-base/embedder.py and answers
tax questions using MMR retrieval + cross-encoder reranking + GPT-4o-mini.

Compatible with the raw FAISS format saved by embedder.py
(files: vector_store/AY2024-25.faiss + AY2024-25.meta.json)

Exposes:
  GET  /health
  GET  /indexes
  POST /query
  POST /query/chunks
"""

import os
import json
import re
import math
import numpy as np
import sys
from typing import Dict, Any, List, Optional
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # /app in Docker
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

VECTOR_STORE_DIR = Path(os.getenv("VECTOR_STORE_DIR", "/app/vector_store"))
DEFAULT_AY       = os.getenv("DEFAULT_AY", "AY2026-27")
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
KB_PDFS_DIR      = Path(os.getenv("KB_PDFS_DIR", "/app/knowledge-base/pdfs"))

app = FastAPI(title="RAG Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_index_cache, _meta_cache, _embedder_cache = {}, {}, {}

# ── PDF filename resolver ─────────────────────────────────────────────────────
# Maps sanitized filename stems (used in chunk_id prefixes) to actual filenames.
# This lets us build clickable download links for PDF-sourced citations at runtime
# without needing to patch the FAISS metadata file.

_pdf_stem_map: dict[str, str] = {}
_pdf_page_map: dict[str, int] = {}  # chunk_id -> page number

def _build_pdf_stem_map():
    """Scan the knowledge-base pdfs directory and build stem -> filename map."""
    global _pdf_stem_map
    if not KB_PDFS_DIR.exists():
        print(f"PDF dir not found: {KB_PDFS_DIR}")
        return
    for f in KB_PDFS_DIR.glob("*.pdf"):
        sanitized = re.sub(r"[^\w]", "_", f.stem.lower())[:40]
        _pdf_stem_map[sanitized] = f.name
    print(f"PDF stem map: {len(_pdf_stem_map)} files indexed")

def _load_page_map():
    """Load chunk_id -> page_number mapping from page_map.json."""
    global _pdf_page_map
    page_map_path = KB_PDFS_DIR.parent / "vector_store" / "page_map.json"
    if not page_map_path.exists():
        print(f"Page map not found: {page_map_path} (PDF links won't have page anchors)")
        return
    with open(page_map_path, encoding="utf-8") as f:
        _pdf_page_map = json.load(f)
    print(f"Page map loaded: {len(_pdf_page_map)} chunk-to-page mappings")

def _resolve_pdf_filename(chunk_id: str) -> str:
    """Given a chunk_id, return the PDF filename if it came from a PDF, else ''."""
    for stem, filename in _pdf_stem_map.items():
        if chunk_id.startswith(stem):
            return filename
    return ""

def _resolve_pdf_page(chunk: dict) -> int:
    """Get the PDF page number for a chunk. Checks metadata first, then page map."""
    # 1. Check if chunk metadata already has pdf_page (from new ingestion)
    page = chunk.get("pdf_page", 0)
    if page:
        return page
    # 2. Fall back to the page map lookup (for existing data)
    chunk_id = chunk.get("chunk_id", "")
    return _pdf_page_map.get(chunk_id, 0)


def _get_embedder(backend="huggingface"):
    if backend in _embedder_cache:
        return _embedder_cache[backend]
    if backend == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        def embed(texts):
            resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
            return np.array([r.embedding for r in resp.data], dtype=np.float32)
        _embedder_cache[backend] = embed
        return embed
    else:
        from sentence_transformers import SentenceTransformer
        print(f"Loading SentenceTransformer: BAAI/bge-small-en-v1.5")
        model = SentenceTransformer("BAAI/bge-small-en-v1.5")
        def embed(texts):
            return model.encode(texts, normalize_embeddings=True, show_progress_bar=False).astype(np.float32)
        _embedder_cache[backend] = embed
        return embed


def _load_index(ay=DEFAULT_AY):
    if ay in _index_cache:
        return _index_cache[ay], _meta_cache[ay]
    import faiss
    index_path = VECTOR_STORE_DIR / f"{ay}.faiss"
    meta_path  = VECTOR_STORE_DIR / f"{ay}.meta.json"
    if not index_path.exists():
        raise FileNotFoundError(f"FAISS index not found: {index_path}. Run: python knowledge-base/embedder.py --ay {ay}")
    index = faiss.read_index(str(index_path))
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    _index_cache[ay] = index
    _meta_cache[ay]  = meta
    print(f"Loaded FAISS [{ay}] — {index.ntotal} vectors")
    return index, meta


def _mmr(query, index, meta, embed_fn, top_k=5, fetch_k=15, lam=0.6):
    q_emb = embed_fn([query])
    distances, ids = index.search(q_emb, fetch_k)
    candidates = []
    for dist, vid in zip(distances[0], ids[0]):
        if vid >= 0:
            c = dict(meta.get(str(vid), {}))
            c["_l2"] = float(dist)
            candidates.append(c)
    if not candidates:
        return []
    cand_embs = embed_fn([c["text"] for c in candidates])
    q_sims = (cand_embs @ q_emb.T).flatten()
    selected, remaining = [], list(range(len(candidates)))
    for _ in range(min(top_k, len(candidates))):
        if not remaining: break
        if not selected:
            best = max(remaining, key=lambda i: q_sims[i])
        else:
            sel_e = cand_embs[selected]
            scores = [(i, lam*q_sims[i] - (1-lam)*float(np.max(cand_embs[i] @ sel_e.T))) for i in remaining]
            best = max(scores, key=lambda x: x[1])[0]
        selected.append(best); remaining.remove(best)
    return [candidates[i] for i in selected]


def _rerank(query, chunks):
    try:
        from sentence_transformers import CrossEncoder
        ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        scores = ce.predict([[query, c["text"]] for c in chunks])
        for c, s in zip(chunks, scores): c["_score"] = float(s)
        chunks.sort(key=lambda x: x.get("_score", 0), reverse=True)
    except Exception:
        pass
    return chunks


# ── ASV RAG: self-verification step ───────────────────────────────────────────
# A distinct step between retrieval and answering: score how much to trust the
# retrieved grounding *before* generating an answer, instead of asking the LLM
# to notice weak grounding on its own. Low-confidence retrieval is flagged on
# the response and prepended to the answer deterministically — not left to the
# model's discretion.

CONFIDENCE_THRESHOLD = 0.5

def _verify_retrieval(chunks: list) -> dict:
    """
    Confidence scoring on retrieved context:
      - No chunks -> confidence 0.
      - Cross-encoder rerank scores present -> sigmoid-squash the top score
        into (0, 1) as a proxy relevance probability (rerank logits are
        unbounded; 0 is the neutral/borderline point). Also checks
        consistency: if the top two chunks are nearly indistinguishable in
        score, that ambiguity discounts confidence further.
      - No rerank scores (reranker unavailable) -> fall back to a
        deliberately conservative, distance-only estimate.
    """
    if not chunks:
        return {"confidence": 0.0, "status": "no_context", "reason": "No chunks retrieved."}

    scores = [c["_score"] for c in chunks if c.get("_score") is not None]

    if scores:
        confidence = 1 / (1 + math.exp(-scores[0]))
        if len(scores) > 1 and abs(scores[0] - scores[1]) < 0.5:
            confidence *= 0.85  # top two chunks barely distinguishable -> less sure
    else:
        l2s = [c["_l2"] for c in chunks if c.get("_l2") is not None]
        confidence = max(0.0, 0.6 - min(l2s) * 0.1) if l2s else 0.3

    confidence = round(min(confidence, 1.0), 3)
    status = "verified" if confidence >= CONFIDENCE_THRESHOLD else "low_confidence"
    reason = (
        "Retrieved context is well-matched to the question."
        if status == "verified"
        else "Retrieved context is a weak or ambiguous match — treat the answer as tentative."
    )
    return {"confidence": confidence, "status": status, "reason": reason}


def _answer(query, chunks, ay, verification: dict = None):
    if not chunks:
        return f"I couldn't find anything in the {ay} knowledge base to answer that."

    ctx = "\n\n---\n\n".join(f"[{c.get('source','')} | {c.get('section','')}]\n{c['text']}" for c in chunks)

    system = f"""You are a grounded question-answering assistant for Indian income tax rules and ITR-1 filing procedure, for assessment year {ay}.

Answer using ONLY the provided context. You explain tax LAW and PROCEDURE (deduction rules, eligibility conditions, exemption rules, filing steps) — you never compute anyone's tax liability yourself; a separate deterministic engine handles all numeric computation elsewhere in this system.

Rules:
- If the context answers the question, answer plainly and cite which source you drew from.
- If the context does not contain enough information, say so explicitly rather than guessing or filling gaps from general knowledge.
- Do not invent section numbers, thresholds, or amounts not present in the context."""

    prompt = f"Context:\n{ctx}\n\nQuestion: {query}\n\nAnswer:"
    try:
        from shared.llm_client import complete_with_system
        answer = complete_with_system(system=system, user=prompt, temperature=0.0)
    except Exception as e:
        # All LLM providers failed — return best chunk as plain text answer
        print(f"LLM fallback exhausted: {e}")
        answer = chunks[0]["text"]

    if verification and verification["status"] == "low_confidence":
        answer = (
            f"⚠️ Low-confidence retrieval ({verification['confidence']:.0%}) — the matched "
            f"context may not directly answer this question. Verify against the cited source "
            f"before relying on this.\n\n{answer}"
        )
    return answer


_user_results = {} # {session_id: {name, regime, taxable_income, tax, tds, refund, explanation}}

class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    ay:       str  = DEFAULT_AY
    top_k:    int  = 5
    backend:  str  = "huggingface"
    rerank:   bool = True

class QueryResponse(BaseModel):
    answer: str; citations: list[dict]; chunks: list[dict]; ay: str
    confidence: float = 1.0
    verification_status: str = "verified"

class StoreUserResultRequest(BaseModel):
    session_id: str
    name: str
    regime: str
    taxable_income: float
    tax: float
    tds: float
    refund: float
    explanation: str

@app.post("/store-user-result")
async def store_user_result(req: StoreUserResultRequest):
    _user_results[req.session_id] = req.dict()
    return {"status": "ok", "message": "Result stored for session"}

@app.get("/health")
def health():
    return {"status": "ok", "service": "rag-service", "default_ay": DEFAULT_AY}

@app.get("/indexes")
def list_indexes():
    if not VECTOR_STORE_DIR.exists(): return {"indexes": []}
    return {"indexes": [f.stem for f in VECTOR_STORE_DIR.glob("*.faiss")]}

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    # Check for stored user result first (CRITICAL REQUIREMENT)
    user_result = _user_results.get(req.session_id) if req.session_id else None
    
    if user_result:
        # Create a "pseudo-chunk" from the stored result
        text = (f"ITR-1 Result for {user_result['name']}:\n"
                f"- Regime: {user_result['regime']}\n"
                f"- Taxable Income: ₹{user_result['taxable_income']:,}\n"
                f"- Total Tax Liability: ₹{user_result['tax']:,}\n"
                f"- TDS Deducted: ₹{user_result['tds']:,}\n"
                f"- Estimated Refund: ₹{user_result['refund']:,}\n"
                f"- Explanation: {user_result['explanation']}")
        
        chunks = [{"text": text, "source": "user_itr_result", "section": "Summary"}]
        answer = _answer(req.question, chunks, req.ay)
        return QueryResponse(
            answer=answer, citations=[{"source": "System", "url": "internal", "section": "Verified Result"}],
            ay=req.ay, chunks=chunks, confidence=1.0, verification_status="verified")

    try:
        index, meta = _load_index(req.ay)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    embed_fn = _get_embedder(req.backend)
    chunks   = _mmr(req.question, index, meta, embed_fn, req.top_k)
    if req.rerank and chunks:
        chunks = _rerank(req.question, chunks)
    verification = _verify_retrieval(chunks)
    answer   = _answer(req.question, chunks, req.ay, verification=verification)
    seen, citations = set(), []
    for c in chunks:
        url = c.get("url", "")
        source = c.get("source", "")
        pdf_filename = c.get("pdf_filename", "")

        # Resolve PDF filename at runtime from chunk_id if not in metadata
        if not url and not pdf_filename:
            pdf_filename = _resolve_pdf_filename(c.get("chunk_id", ""))

        # Build a download URL for PDF sources that lack a web URL
        if not url and pdf_filename:
            page = _resolve_pdf_page(c)
            page_anchor = f"#page={page}" if page else ""
            url = f"/api/pdfs/{pdf_filename}{page_anchor}"

        dedup_key = url.split("#")[0] if url else source  # dedup by base URL, not page
        if dedup_key and dedup_key not in seen:
            citations.append({"source": source, "url": url if url else "#", "section": c.get("section", "")})
            seen.add(dedup_key)
    return QueryResponse(
        answer=answer, citations=citations, ay=req.ay,
        confidence=verification["confidence"], verification_status=verification["status"],
        chunks=[{"text":c["text"],"source":c.get("source",""),"section":c.get("section","")} for c in chunks])

@app.post("/query/chunks")
async def query_chunks_only(req: QueryRequest):
    try: index, meta = _load_index(req.ay)
    except FileNotFoundError as e: raise HTTPException(404, str(e))
    chunks = _mmr(req.question, index, meta, _get_embedder(req.backend), req.top_k)
    return {"chunks": chunks, "count": len(chunks)}

@app.on_event("startup")
async def startup():
    try:
        _build_pdf_stem_map()
        _load_page_map()
        _load_index(DEFAULT_AY)
        print("Pre-loading models...")
        _get_embedder("huggingface")
        from sentence_transformers import CrossEncoder
        CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        print("All models loaded.")
    except Exception as e:
        print(f"Warning during startup: {e}")
