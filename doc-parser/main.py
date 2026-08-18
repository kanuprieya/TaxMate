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

from parsers.form16 import parse_form16, parse_form16_from_text, form16_to_dict
from parsers.form26as import parse_form26as
from parsers.ais import parse_ais
from parsers.tis import parse_tis
from parsers.bank_statement import parse_bank_statement, parse_bank_statement_from_rows
# ITR-2-only document types (capital gains / house property / foreign income)
from parsers.capital_gains import parse_capital_gains
from parsers.property import parse_property
from parsers.foreign_income import parse_foreign_income
# "Other Inputs, Deductions & Disclosures" document types
from parsers.health_insurance import parse_health_insurance
from parsers.life_insurance import parse_life_insurance
from parsers.home_loan import parse_home_loan
from parsers.other_sources_income import parse_other_sources_income
from parsers.residential_status import parse_residential_status

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import xlrd  # legacy .xls (binary format) — openpyxl only reads the OOXML .xlsx/.xlsm zip format
except ImportError:
    xlrd = None

LEGACY_XLS_EXTENSIONS = {".xls"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm"} | LEGACY_XLS_EXTENSIONS
EXCEL_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroEnabled.12",  # .xlsm
    "application/vnd.ms-excel",  # .xls — common real-world case: older broker/CAMS
                                  # capital-gains exports are frequently this legacy format
    # Some browsers/OSes send this generic type for .xlsx/.xlsm instead of
    # the correct ones above — accepted here only in combination with a
    # matching filename extension (see _validate_file).
    "application/octet-stream",
}

# Doc types that are pure supporting context (a CA firm's information
# request, an FX reference-rate table) rather than a source of tax figures
# to extract. Accepted and stored so they're part of the filer's uploaded
# document set, but never parsed for structured data and never scanned for
# foreign-currency signals (an SBI TT rate table's entire content IS a list
# of foreign currency codes — scanning it would always false-positive the
# out-of-scope redirect below).
REFERENCE_ONLY_DOC_TYPE = "REFERENCE_DOCUMENT"

# A raw-text signal that a document — one that's supposed to represent the
# filer's own domestic income (salary/bank/other-sources) — is actually
# foreign-currency-denominated. graph/router.py's is_out_of_scope() checks
# this flag across every uploaded document, not just ones explicitly typed
# foreign_income, so foreign salary/interest hiding in a mislabeled upload
# (found against a real "Salary workings...AED" spreadsheet uploaded to the
# Form 16 slot) gets the same flag-and-redirect treatment instead of being
# silently mis-parsed as Rs 0.
FOREIGN_CURRENCY_SIGNAL_PATTERN = re.compile(
    r"\b(USD|GBP|EUR|AED|SGD|AUD|CAD|CHF|JPY|SAR|QAR|KWD|OMR|BHD)\b|Exchange\s+Rate",
    re.IGNORECASE,
)
DOC_TYPES_CHECKED_FOR_FOREIGN_CURRENCY = {"FORM16", "BANKSTMT", "BANK_STATEMENT", "OTHER_SOURCES_INCOME"}

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
        # ITR-2-only document types
        "CAPITAL_GAINS": ["Capital Gains Statement", "Short Term Capital Gain", "Long Term Capital Gain", "Realized Gain", "Contract Note"],
        "PROPERTY":      ["Interest Certificate", "Property Address", "Municipal Tax", "Annual Value", "Rent Received", "Tenant Name"],
        "FOREIGN_INCOME": ["Schedule FA", "Foreign Asset", "Form 67", "Foreign Tax Credit", "Country of Residence"],
        # "Other Inputs, Deductions & Disclosures" document types
        "HEALTH_INSURANCE": ["Health Insurance", "Mediclaim", "Policy Premium", "Sum Insured"],
        "LIFE_INSURANCE":   ["Life Insurance", "Policy Premium", "Sum Assured", "Premium Receipt"],
        "HOME_LOAN":        ["Provisional Certificate", "Principal Repaid", "Principal Amount", "Loan Account Number"],
        "OTHER_SOURCES_INCOME": ["Dividend received", "Savings Bank Interest", "Fixed Deposit Interest", "Dividend Income"],
        "RESIDENTIAL_STATUS":   ["Residential Status", "Ordinarily Resident", "Days in India", "Section 6"],
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
    
    is_excel = Path(file.filename or "").suffix.lower() in EXCEL_EXTENSIONS

    try:
        # 1. Extract text for detection
        if is_excel:
            raw_text = _extract_excel_text(path)
            print(f"[FORM16-DEBUG] Detection-stage text: {len(raw_text)} chars from .xlsx workbook", flush=True)
        else:
            raw_text = ""
            with pdfplumber.open(path) as pdf:
                page_count = len(pdf.pages)
                # Every page, not just the first 3: this same raw_text is what
                # CAPITAL_GAINS/PROPERTY/FOREIGN_INCOME/FORM26AS/AIS/TIS parse
                # against below (FORM16 and BANKSTMT independently re-read the
                # full file instead). A 3-page cap silently dropped any real
                # broker statement's transaction table sitting past a cover
                # letter/disclaimer — found against a real Kotak Securities
                # capital gains statement that only had P&L data on page 4+.
                for page in pdf.pages:
                    raw_text += (page.extract_text() or "")
            print(f"[FORM16-DEBUG] Detection-stage text: {len(raw_text)} chars from all "
                  f"{page_count} page(s)", flush=True)
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
        # FORM16 and BANKSTMT normally re-open the saved file with pdfplumber
        # directly (rather than reusing raw_text) — for .xlsx uploads they
        # use the same underlying extraction (parse_form16_from_text /
        # parse_bank_statement_from_rows) fed from the workbook instead.
        if doc_type == "FORM16":
            data = parse_form16_from_text(raw_text) if is_excel else parse_form16(str(path))
            result = form16_to_dict(data)
        elif doc_type == "FORM26AS":
            result = parse_form26as(raw_text)
        elif doc_type == "AIS":
            result = parse_ais(raw_text)
        elif doc_type == "TIS":
            result = parse_tis(raw_text)
        elif doc_type == "BANKSTMT" or doc_type == "BANK_STATEMENT":
            result = parse_bank_statement_from_rows(_extract_excel_rows(path)) if is_excel else parse_bank_statement(str(path))
            doc_type = "BANKSTMT"
        elif doc_type == "CAPITAL_GAINS":
            result = parse_capital_gains(raw_text)
        elif doc_type == "PROPERTY":
            result = parse_property(raw_text)
        elif doc_type == "FOREIGN_INCOME":
            result = parse_foreign_income(raw_text)
        elif doc_type == "HEALTH_INSURANCE":
            result = parse_health_insurance(raw_text)
        elif doc_type == "LIFE_INSURANCE":
            result = parse_life_insurance(raw_text)
        elif doc_type == "HOME_LOAN":
            result = parse_home_loan(raw_text)
        elif doc_type == "OTHER_SOURCES_INCOME":
            result = parse_other_sources_income(raw_text)
        elif doc_type == "RESIDENTIAL_STATUS":
            result = parse_residential_status(raw_text)
        elif doc_type == REFERENCE_ONLY_DOC_TYPE:
            # Not a source of tax figures (a CA firm's information request, an
            # FX reference-rate table) — accepted and kept with the session,
            # never parsed for structured data.
            result = {
                "doc_type": "reference_document",
                "parse_confidence": 1.0,
                "warnings": [],
                "note": "Stored for reference — not used in tax computation.",
            }
        elif is_excel:
            raise HTTPException(status_code=422, detail="Unsupported or unrecognized spreadsheet format.")
        else:
            # Final fallback: try Form 16
            try:
                data = parse_form16(str(path))
                result = form16_to_dict(data)
                doc_type = "FORM16"
            except:
                raise HTTPException(status_code=422, detail="Unsupported or unrecognized document format.")

        # 4. Foreign-currency signal — only for doc types meant to represent
        # domestic income; a reference-rate table or an explicitly-typed
        # foreign_income upload are excluded (see constant definitions above).
        # Rather than just warning, re-run the actual foreign-income
        # extraction on this same text and reclassify the document — a
        # "Salary workings...AED" spreadsheet uploaded to the Form 16 slot
        # is real foreign salary income that Schedule FSI can compute, not
        # a rejected upload. graph/router.py still redirects Non-Resident/
        # RNOR filers regardless of what this produces.
        if doc_type in DOC_TYPES_CHECKED_FOR_FOREIGN_CURRENCY and FOREIGN_CURRENCY_SIGNAL_PATTERN.search(raw_text):
            result = parse_foreign_income(raw_text)
            result["foreign_currency_signal"] = True
            doc_type = "FOREIGN_INCOME"

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
    return await unified_parse_endpoint(file, session_id, None)

@app.post("/parse/bank-statement")
async def legacy_bank(file: UploadFile = File(...), session_id: str = Form(None)):
    return await unified_parse_endpoint(file, session_id, None)

# ── Helpers ────────────────────────────────────────────────────────────────────

async def _validate_file(file: UploadFile):
    ext = Path(file.filename or "").suffix.lower()
    if ext in EXCEL_EXTENSIONS:
        if file.content_type not in EXCEL_CONTENT_TYPES:
            raise HTTPException(400, "Only PDF, JPG, PNG, XLSX, XLSM, XLS allowed")
        return
    if file.content_type not in {"application/pdf", "image/jpeg", "image/png"}:
        raise HTTPException(400, "Only PDF, JPG, PNG, XLSX, XLSM, XLS allowed")


def _extract_excel_text(path: Path) -> str:
    """Flattens every sheet into plain text (one line per row, cells joined
    by a single space) so the existing text-based parsers — the LLM paths
    (form16.py/capital_gains.py) plus the regex-only ones (property.py/
    foreign_income.py) — can read a spreadsheet exactly like they read
    pdfplumber's extract_text() output, which likewise separates a table
    row's cells with plain whitespace, not a delimiter. That match matters:
    several of those parsers' regexes require their label and amount to be
    separated only by `[:\\s]*` (no wildcard) — a punctuation delimiter here
    would silently break them the same way an unmarked currency amount did
    (see property.py's CURRENCY-marker comment for that earlier bug). No
    column-name assumptions are made here; broker/bank/employer exports
    don't share a common schema, so interpreting the columns is left to
    each parser (LLM extraction, where available, reads the whole row for
    context rather than assuming a fixed position).

    .xls (legacy binary format, common for older broker/CAMS capital-gains
    exports) is a completely different file format from .xlsx/.xlsm despite
    the similar extension — openpyxl only reads the OOXML zip format, so
    that branch goes through xlrd instead."""
    if Path(path).suffix.lower() in LEGACY_XLS_EXTENSIONS:
        return "\n".join(_iter_xls_rows_as_lines(path))
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed — cannot parse .xlsx files")
    # read_only workbooks hold the underlying zip file open until closed
    # explicitly — on Windows that leaves the temp upload locked, so the
    # caller's later path.unlink() fails with a PermissionError. close()
    # in finally releases it regardless of how the loop above exits.
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        lines = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    lines.append(" ".join(cells))
    finally:
        wb.close()
    return "\n".join(lines)


def _iter_xls_rows_as_lines(path: Path) -> list:
    if xlrd is None:
        raise RuntimeError("xlrd is not installed — cannot parse legacy .xls files")
    book = xlrd.open_workbook(str(path))
    lines = []
    for sheet in book.sheets():
        for row_idx in range(sheet.nrows):
            cells = [str(c.value) for c in sheet.row(row_idx) if c.value not in (None, "")]
            if cells:
                lines.append(" ".join(cells))
    return lines


def _extract_excel_rows(path: Path) -> list:
    """Raw row/cell data (all sheets concatenated) for parsers that classify
    row-by-row — currently only parse_bank_statement_from_rows, which mirrors
    the same [Date, Description, Debit, Credit, Balance]-style row shape a
    pdfplumber table extraction produces. Every other .xlsx-aware parser
    consumes flattened text from _extract_excel_text instead."""
    if Path(path).suffix.lower() in LEGACY_XLS_EXTENSIONS:
        if xlrd is None:
            raise RuntimeError("xlrd is not installed — cannot parse legacy .xls files")
        book = xlrd.open_workbook(str(path))
        rows = []
        for sheet in book.sheets():
            for row_idx in range(sheet.nrows):
                rows.append([c.value if c.value != "" else None for c in sheet.row(row_idx)])
        return rows
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed — cannot parse .xlsx files")
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        rows = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
    finally:
        wb.close()
    return rows

async def _save_upload(file: UploadFile) -> Path:
    ext = Path(file.filename).suffix
    tmp_path = UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
    with open(tmp_path, "wb") as buffer:
        buffer.write(await file.read())
    return tmp_path
