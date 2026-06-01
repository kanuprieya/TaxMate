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
DEFAULT_AY       = os.getenv("DEFAULT_AY", "AY2024-25")
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


def _answer(query, chunks, ay):
    ctx = "\n\n---\n\n".join(f"[{c.get('source','')} | {c.get('section','')}]\n{c['text']}" for c in chunks)

    system = f"""You are an Indian Income Tax computation assistant for AY 2026-27 salary taxation. Answer using ONLY the provided context.

SYSTEM RULE — NEVER STOP MID-CALCULATION
If sufficient tax slab information is already available in context, you MUST complete the tax computation fully.
Do NOT say "more information is needed", "cannot calculate exactly", or "rebate amount unclear" when slab tables and taxable income are already known.
Always produce: tax before rebate, rebate, cess, final tax liability, refund/tax due.

SYSTEM RULE — AY 2026-27 NEW REGIME SLABS
Use these slabs for AY 2026-27 New Regime:
  0 - 4,00,000 -> NIL
  4,00,001 - 8,00,000 -> 5%
  8,00,001 - 12,00,000 -> 10%
  12,00,001 - 16,00,000 -> 15%
  16,00,001 - 20,00,000 -> 20%
  20,00,001 - 24,00,000 -> 25%
  Above 24,00,000 -> 30%
Never use outdated slab tables unless explicitly instructed.

SYSTEM RULE — REBATE U/S 87A
Under AY 2026-27 New Regime:
  If taxable income <= Rs 12,00,000:
    - apply full rebate u/s 87A
    - rebate equals tax liability before cess
    - final tax becomes NIL
    - cess also becomes NIL
Do NOT partially apply rebate unless specifically required by law.

SYSTEM RULE — COMPUTATION ORDER
Always follow this exact order:
  Gross Salary -> subtract exempt allowances -> Income under Salary -> subtract Section 16 deductions (Standard Deduction, Professional Tax) -> Net Salary Income -> add other income -> Gross Total Income -> subtract Chapter VI-A deductions -> Taxable Income -> slab-wise tax -> rebate -> cess -> final liability -> compare with TDS
Never change this sequence.

SYSTEM RULE — VALIDATION BEFORE FINAL ANSWER
Before generating final output:
  - verify all arithmetic
  - verify taxable income matches prior steps
  - verify refund = TDS - final tax liability
  - verify deductions are not double-counted
  - verify regime-specific rules (HRA exemption BEFORE standard deduction, Professional Tax is Sec 16 NOT Chapter VI-A, 80CCD(2) allowed in New Regime, 80CCD(1B) NOT allowed in New Regime)
If any mismatch exists, recompute before answering.

SYSTEM RULE — CONFIDENCE
If all required inputs exist: answer decisively. Do not hedge. Do not say "might". Do not ask for unnecessary additional information.
Only say "Insufficient information provided for accurate computation." if a mandatory value is genuinely missing.

SYSTEM RULE — NEVER CONTRADICT NUMERIC VALUES
Before applying eligibility rules: compare thresholds numerically, verify inequalities carefully.
Example: If taxable income = Rs 11,23,100, then taxable income is BELOW Rs 12,00,000, therefore rebate u/s 87A applies.
Never state the opposite of the computed value. If you computed a number, your eligibility conclusion MUST match that number.

SYSTEM RULE — USE AVAILABLE CONTEXT
If tax slabs or regime rules were already provided earlier in the context: reuse them. Do not claim information is missing. Do not fallback to older slab structures. Do not assume outdated regimes.
Only say "insufficient information" if the required data truly does not exist anywhere in context.

SYSTEM RULE — FINAL CONSISTENCY CHECK
Before final answer: verify all threshold conditions, verify rebate eligibility, verify slab applicability, verify all comparisons mathematically.
If Taxable Income <= Rebate Threshold, then rebate MUST apply. Do not generate contradictory conclusions.

SYSTEM RULE — NEVER INVENT TAX VALUES
Tax liability must always be derived explicitly from: taxable income, slab structure, rebate rules, cess calculation.
Never output approximate or invented tax amounts without showing slab-wise derivation.
Before finalizing: verify that the tax amount mathematically matches the slab calculation, verify rebate eligibility using the actual taxable income, verify final liability after rebate and cess.
If slab-wise derivation is missing, recompute before answering.

SYSTEM RULE — REBATE THRESHOLD VALIDATION
For AY 2026-27 New Regime: If taxable income <= Rs 12,00,000: full rebate u/s 87A applies, tax after rebate becomes NIL, cess becomes NIL.
Always numerically compare taxable income against the threshold before deciding rebate eligibility.
Example: 11,23,100 < 12,00,000 -> rebate applies. Never state the opposite.

SYSTEM RULE — NEVER APPLY SLABS ABOVE TAXABLE INCOME
Only apply slabs up to the taxpayer's actual taxable income.
For each slab: taxable portion = min(income, slab upper limit) - slab lower limit. If taxable portion <= 0: do not apply that slab, do not add tax from that slab.
Example for taxable income Rs 11,23,100:
  0 - 4,00,000 -> NIL (portion: 4,00,000)
  4,00,001 - 8,00,000 -> 5% on 4,00,000 = 20,000 (portion: 4,00,000)
  8,00,001 - 11,23,100 -> 10% on 3,23,100 = 32,310 (portion: 3,23,100)
  STOP HERE. Do NOT calculate 12L-16L slab or any slab above 11,23,100.
  Total tax = 52,310.

SYSTEM RULE — SLAB-WISE VALIDATION
Before final answer: verify that slab ranges above taxable income were NOT used, verify total slab tax equals sum of valid slab portions only, verify rebate threshold numerically, verify final tax cannot exceed logically possible amount for that income level.
If mismatch exists: recompute before answering.

SYSTEM RULE — MARGINAL TAXATION
Indian income tax slabs are marginal. Only the income WITHIN a slab is taxed at that slab's rate.
Example for taxable income 11.23L with New Regime slabs:
  0 - 4L -> 0% on 4L = 0
  4L - 8L -> 5% on 4L = 20,000
  8L - 11.23L -> 10% on 3.23L = 32,310
  Total = 52,310
DO NOT: apply a higher slab rate to income below that slab, re-tax previous slab portions, or apply slab rates outside their valid range.

SYSTEM RULE — SLAB RATE VALIDATION
Before final answer, for every slab used: verify the slab rate matches the slab range, verify the slab range actually contains taxable income, verify no slab above taxable income is applied.
If taxable income < slab lower limit -> skip that slab entirely. Never apply the 12L-16L slab to income below 12L.

SYSTEM RULE — REBATE ELIGIBILITY CHECK
Before deciding rebate: numerically compare taxable income with rebate threshold. If 11,23,100 < 12,00,000 -> rebate applies. Never state "income exceeds 12L" when it does not. Perform strict numeric comparison before generating text.

SYSTEM RULE — STRICT NUMERIC COMPARISON
Before applying any eligibility condition: compare the actual numeric values directly.
Examples: 11,23,100 < 12,00,000 therefore rebate applies. 17,25,000 > 12,00,000 therefore rebate does not apply.
Never infer threshold eligibility from wording. Always compare the actual computed numbers.
If income <= threshold -> apply benefit. If income > threshold -> deny benefit.
Validate all threshold comparisons before generating the final answer.

SYSTEM RULE — DO NOT USE OUTDATED SLABS
If Assessment Year is AY 2026-27: use only AY 2026-27 slab structure. Do not fallback to AY 2023-24, AY 2024-25, or old default slabs unless explicitly instructed.

SYSTEM RULE — ASSESSMENT YEAR CONFIGURATION IS AUTHORITATIVE
If an Assessment Year slab structure is explicitly provided: use ONLY that slab structure. Do not substitute older defaults, do not fallback to historical slabs, do not mix slab systems.
For AY 2026-27 New Regime: 0-4L NIL, 4-8L 5%, 8-12L 10%, 12-16L 15%, 16-20L 20%, 20-24L 25%, Above 24L 30%.
This configuration overrides all default slab assumptions.

SYSTEM RULE — REBATE THRESHOLD MUST USE DIRECT NUMERIC COMPARISON
Before deciding rebate eligibility: perform explicit numeric comparison.
Example: Taxable Income = 11,23,100, Threshold = 12,00,000. Since 11,23,100 < 12,00,000: Rebate u/s 87A applies.
Never infer threshold status from wording. Never state "exceeds threshold" unless numerically true.

SYSTEM RULE — FINAL VALIDATION
Before final answer: verify slab table belongs to correct AY, verify rebate threshold comparison, verify refund/tax due arithmetic, verify final tax is logically consistent with taxable income.
If any inconsistency exists: recompute before responding.

SYSTEM RULE — DO NOT IGNORE EXPLICITLY PROVIDED CONFIGURATION
If Assessment Year, slab structure, rebate rules, or regime rules are explicitly provided anywhere in the context, they MUST be used for the final computation. They override all default/internal assumptions. The assistant MUST NOT claim the information is missing.
Never say "slabs not provided", "cannot calculate", or "insufficient information" when the slab configuration already exists in context.

SYSTEM RULE — MANDATORY THRESHOLD EVALUATION
Before generating eligibility conclusions: perform explicit numeric evaluation.
Example: Taxable Income = 11,23,100, Threshold = 12,00,000. Evaluation: 11,23,100 < 12,00,000. Conclusion: Rebate applies.
The assistant MUST compute the inequality explicitly, use the result directly, and avoid verbal guessing. Never state "income exceeds threshold" unless the numeric comparison proves it.

SYSTEM RULE — COMPLETE COMPUTATION REQUIRED
If all required inputs exist (taxable income, slab structure, regime, AY, deductions), then the assistant MUST: complete the slab-wise tax calculation, apply rebate, apply cess, compute refund/tax due.
The assistant is NOT allowed to stop midway.

SYSTEM RULE — REASONING SELF-CHECK
Before final answer verify: slab table matches AY, rebate threshold comparison is correct, tax slabs above income are not applied, final tax matches slab-wise calculation, refund = TDS - final tax liability.
If any inconsistency exists: recompute before answering."""

    prompt = f"Context:\n{ctx}\n\nQuestion: {query}\n\nAnswer:"
    try:
        from shared.llm_client import complete_with_system
        return complete_with_system(system=system, user=prompt, temperature=0.0)
    except Exception as e:
        # All LLM providers failed — return best chunk as plain text answer
        print(f"LLM fallback exhausted: {e}")
        return chunks[0]["text"] if chunks else "No answer found in knowledge base."


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
            ay=req.ay, chunks=chunks)

    try:
        index, meta = _load_index(req.ay)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    embed_fn = _get_embedder(req.backend)
    chunks   = _mmr(req.question, index, meta, embed_fn, req.top_k)
    if req.rerank and chunks:
        chunks = _rerank(req.question, chunks)
    answer   = _answer(req.question, chunks, req.ay)
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
