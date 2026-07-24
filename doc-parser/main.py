"""
Doc Parser Service — FastAPI
==============================
Accepts uploaded documents and returns structured JSON.
Implements the Universal Document Parser spec.
"""

import os
import uuid
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.form16 import parse_form16, form16_to_dict
from parsers.form26as import parse_form26as
from parsers.ais import parse_ais
from parsers.tis import parse_tis
from parsers.bank_statement import parse_bank_statement

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

app = FastAPI(title="Doc Parser Service", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/itr1-uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Document Type Detection ───────────────────────────────────────────────────

def detect_document_type(text: str) -> str:
    checks = {
        "FORM16":   ["Certificate under Section 203", "FORM NO. 16", "PART A", "PART B", "TAN of Employer", "Name of Employer"],
        "FORM26AS": ["FORM 26AS", "Tax Credit Statement", "TAN of Deductor", "Amount Paid/Credited"],
        "AIS":      ["Annual Information Statement", "SFT-", "Reported Value", "Modified Value"],
        "TIS":      ["Taxpayer Information Summary", "Processed Value", "Accepted by Taxpayer"],
        "BANKSTMT": ["Account Number", "Transaction Date", "Debit", "Credit", "Balance"],
    }
    text_upper = text.upper()
    for doc_type, keywords in checks.items():
        # Require at least 2 keywords to match for high confidence
        if sum(1 for kw in keywords if kw.upper() in text_upper) >= 2:
            return doc_type
    return "UNKNOWN"

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "doc-parser", "engine": "Universal Parser v1"}

@app.post("/parse")
@app.post("/parse/auto")
async def unified_parse_endpoint(
    file:       UploadFile = File(...),
    session_id: Optional[str] = Form(default=None),
    hint:       Optional[str] = Form(default=None),
):
    """Unified endpoint: detects doc type automatically and parses."""
    print(f"DEBUG: Received parse request for {file.filename}, hint={hint}")
    await _validate_file(file)
    path = await _save_upload(file)
    
    try:
        # 1. Extract text for detection
        raw_text = ""
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages[:3]:
                raw_text += (page.extract_text() or "")
        print(f"[FORM16-DEBUG] Detection-stage text: {len(raw_text)} chars from first "
              f"{min(3, page_count)} of {page_count} page(s)", flush=True)
        print(f"[FORM16-DEBUG] Detection-stage first 500 chars: {raw_text[:500]!r}", flush=True)

        # 2. Detect type
        detected_type = detect_document_type(raw_text)
        print(f"DEBUG: Detected type: {detected_type}")
        
        # Use hint if detection is UNKNOWN or if hint is strong
        doc_type = detected_type
        if doc_type == "UNKNOWN" and hint:
            doc_type = hint.upper()
            print(f"DEBUG: Using hint: {doc_type}")
        
        # 3. Route to parser
        if doc_type == "FORM16":
            data = parse_form16(str(path))
            result = form16_to_dict(data)
        elif doc_type == "FORM26AS":
            result = parse_form26as(raw_text)
        elif doc_type == "AIS":
            result = parse_ais(raw_text)
        elif doc_type == "TIS":
            result = parse_tis(raw_text)
        elif doc_type == "BANKSTMT" or doc_type == "BANK_STATEMENT":
            result = parse_bank_statement(str(path))
            doc_type = "BANKSTMT"
        else:
            # Final fallback: try Form 16
            try:
                data = parse_form16(str(path))
                result = form16_to_dict(data)
                doc_type = "FORM16"
            except:
                raise HTTPException(status_code=422, detail="Unsupported or unrecognized document format.")

        response = {
            "success":    True,
            "doc_type":   doc_type.lower() if doc_type != "BANKSTMT" else "bank_statement",
            "session_id": session_id or str(uuid.uuid4()),
            "data":       result,
            "confidence": result.get("parse_confidence", 0.5),
            "warnings":   result.get("warnings", []),
        }
        print(f"[FORM16-DEBUG] Final response returned to caller:\n{json.dumps(response, indent=2, default=str)}", flush=True)
        return response

    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Parse failed: {str(e)}")
    finally:
        path.unlink(missing_ok=True)

# ── Legacy Endpoints (Proxies to Unified) ──────────────────────────────────────

@app.post("/parse/form16")
async def legacy_form16(file: UploadFile = File(...), session_id: str = Form(None)):
    return await unified_parse_endpoint(file, session_id)

@app.post("/parse/bank-statement")
async def legacy_bank(file: UploadFile = File(...), session_id: str = Form(None)):
    return await unified_parse_endpoint(file, session_id)

# ── Helpers ────────────────────────────────────────────────────────────────────

async def _validate_file(file: UploadFile):
    if file.content_type not in {"application/pdf", "image/jpeg", "image/png"}:
        raise HTTPException(400, "Only PDF, JPG, PNG allowed")

async def _save_upload(file: UploadFile) -> Path:
    ext = Path(file.filename).suffix
    tmp_path = UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
    with open(tmp_path, "wb") as buffer:
        buffer.write(await file.read())
    return tmp_path
