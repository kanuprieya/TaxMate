import json
import re
import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))  # /app in Docker — for `shared.llm_client`

class Form16Data(BaseModel):
    """Numeric fields default to None, meaning 'not found on this document' —
    distinct from 0.0, which means the document explicitly states a zero/nil
    value. Collapsing these was the root of the "—" vs ₹0 ambiguity in the
    UI: a genuinely-missing field and a confirmed-zero field must stay
    distinguishable all the way from extraction through to display."""
    employee_name: Optional[str] = None
    employee_pan: Optional[str] = None
    assessment_year: Optional[str] = None
    tax_regime_llm: Optional[str] = None  # "old" | "new", set only by LLM extraction
    gross_salary: Optional[float] = None
    salary_as_per_17_1: Optional[float] = None
    perquisites_17_2: Optional[float] = None
    profits_17_3: Optional[float] = None
    hra_10_13a: Optional[float] = None
    hra_received: Optional[float] = None
    lta_10_5: Optional[float] = None
    other_exempt_10: Optional[float] = None
    total_exempt_10: Optional[float] = None
    standard_deduction_16ia: Optional[float] = None
    professional_tax_16iii: Optional[float] = None
    income_under_salary: Optional[float] = None
    house_property_loss: Optional[float] = None
    other_sources_income: Optional[float] = None
    sec_80c_claimed: Optional[float] = None
    sec_80ccd_1_claimed: Optional[float] = None
    sec_80d_claimed: Optional[float] = None
    total_vi_a_claimed: Optional[float] = None
    taxable_income_form16: Optional[float] = None
    tax_payable_form16: Optional[float] = None
    tds_deducted_form16: Optional[float] = None
    raw_text_snippet: str = ""

PART_A_PATTERNS = {
    "employee_pan":        [r"PAN\s+of\s+(?:the\s+)?(?:Employee|Deductee)[:\s]+([\w\d]+)"],
    "employee_name":       [
        r"Name\s+of\s+(?:the\s+)?(?:Employee|Deductee)[\s\r\n]+([^\n]+)",
        r"Employee\s+Name[:\s]+([^\n]+)",
        r"Name\s+of\s+the\s+Employee[:\s]*([^\n]+)",
        r"NAME\s+OF\s+EMPLOYEE[:\s]*([^\n]+)",
        r"PAN\s+of\s+Employee.*?Name\s+of\s+Employee[:\s]*([^\n]+)",
        r"Name\s+([A-Z\s]+?)\s+PAN"
    ],
    "assessment_year":     [r"Assessment\s+Year[:\s]+(20\d{2}-\d{2,4}|\d{4}-\d{2,4})"],
    "period_from":         [r"Period\s+From[:\s]+([^\n]+?)\s+To"],
    "period_to":           [r"Period\s+.*?To[:\s]+([^\n]+)"],
}

PART_B_PATTERNS = {
    "salary_as_per_17_1":         [r"17\(1\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Salary\s+as\s+per.*?17\(1\)[\s\.\:]*([\d,]+\.?\d*)"],
    "perquisites_17_2":           [r"17\(2\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Value\s+of\s+perquisites.*?17\(2\)[\s\.\:]*([\d,]+\.?\d*)"],
    "profits_17_3":               [r"17\(3\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Profits\s+in\s+lieu.*?17\(3\)[\s\.\:]*([\d,]+\.?\d*)"],
    "gross_salary":               [r"GROSS\s+SALARY\s*(?:\([^\)]+\))?\s*([\d,]+\.?\d*)",
                                   r"Total\s*\(a\s*\+\s*b\s*\+\s*c\s*\)\s*([\d,]+\.?\d*)",
                                   r"Gross\s+Salary[\s\.\:]*([\d,]+\.?\d*)"],
    "hra_10_13a":                 [r"10\(13A\)[^\d\n]*([\d,]{4,}\.?\d*)",
                                   r"(?:House\s+Rent|HRA).*?10\(13A\)[^\d\n]*([\d,]{4,}\.?\d*)"],
    "hra_received":               [r"Actual\s+HRA\s*=\s*Rs\.\s*([\d,]+\.?\d*)",
                                   r"House\s+Rent\s+Allowance\s*\(HRA\)\s*([\d,]+\.?\d*)"],
    "lta_10_5":                   [r"10\(5\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"(?:Leave\s+Travel|LTA).*?10\(5\)[^\d\n]*([\d,]+\.?\d*)"],
    "other_exempt_10":            [r"10\(14\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Other.*?10\(14\)[^\d\n]*([\d,]+\.?\d*)"],
    "total_exempt_10":            [r"Total\s+Exempt\s+Allowances[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total.*?Section\s+10[\s\.\:]*([\d,]+\.?\d*)"],
    "standard_deduction_16ia":    [r"16\(ia\)[\s\.\:]*([\d,]+\.?\d*)",
                                   r"Standard\s+Deduction.*?([\d,]+\.?\d*)"],
    "professional_tax_16iii":     [r"16\(iii\)[\s\.\:]*([\d,]+\.?\d*)",
                                   r"Tax\s+on\s+employment[^\d\n]*([\d,]+\.?\d*)"],
    "income_under_salary":        [r"HEAD\s+'SALARIES'[^\d\n]*([\d,]+\.?\d*)",
                                   r"Income.*?head\s+Salaries[\s\.\:]*([\d,]+\.?\d*)"],
    "house_property_loss":        [r"House\s+Property.*?Section\s+24\(b\)\s*(-?[\d,]+\.?\d*)",
                                   r"Interest\s+on\s+Housing\s+Loan\s*(-?[\d,]+\.?\d*)"],
    "other_sources_income":       [r"Other\s+Sources\s+offered\s+for\s+TDS[\s\.\:]*([\d,]+\.?\d*)",
                                   r"Income\s+under.*?Other\s+Sources[\s\.\:]*([\d,]+\.?\d*)"],
    "sec_80c_claimed":            [r"80C[^\d\n]*([\d,]{4,}\.?\d*)"],
    "sec_80ccd_1_claimed":        [r"80CCD\(1B\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"80CCD\(1\)[^\d\n]*([\d,]+\.?\d*)"],
    "sec_80d_claimed":            [r"80D[^\d\n]*([\d,]+\.?\d*)"],
    "total_vi_a_claimed":         [r"Total\s+Deductions\s+under\s+Chapter\s+VI-A[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total.*?Chapter\s+VI-A[\s\.\:]*([\d,]+\.?\d*)"],
    "taxable_income_form16":      [r"TOTAL\s+INCOME\s*\(5\s*\-\s*6\s*\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total\s+Income[\s\.\:]*([\d,]+\.?\d*)"],
    "tax_payable_form16":         [r"TOTAL\s+TAX\s+AND\s+INTEREST\s+PAYABLE[^\d\n]*([\d,]+\.?\d*)",
                                   r"Net\s+Tax\s+Payable[^\d\n]*([\d,]+\.?\d*)"],
    "tds_deducted_form16":        [r"TDS\s+for\s+the\s+current\s+employer[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total\s+Tax\s+Paid[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total.*?TDS\s+deducted[\s\.\:]*([\d,]+\.?\d*)"],
}

# ── LLM-based extraction (primary path) ────────────────────────────────────────
# Form 16 layouts vary by employer/payroll software, and pdfplumber's raw text
# frequently reorders table cells (values before labels, side-by-side columns
# interleaved into single lines). Regex against that is fragile — see
# parse_form16_text() below, kept only as a fallback for when no LLM provider
# is reachable. An LLM can use context to recover the right field even when
# its literal position in the extracted text is scrambled.

FORM16_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data from an Indian Form 16 (TDS certificate) PDF's raw text. The text was extracted with pdfplumber and may have jumbled column order, merged lines, or values appearing before their labels — read the whole document and use context/reasoning to find the correct value for each field, don't just pattern-match nearby text.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "employee_name": string,               // the EMPLOYEE's full name (not the employer's, not the signatory/deductor's authorized signatory)
  "employee_pan": string,                // the EMPLOYEE's PAN (10-char alphanumeric, e.g. AKLPU4174E) — NOT the Deductor's/Employer's PAN or TAN
  "assessment_year": string,             // format "YYYY-YY", e.g. "2025-26"
  "tax_regime": "old" or "new",          // find the question about "opting out of taxation u/s 115BAC(1A)" AND its Yes/No answer: answer "Yes" (opting OUT of 115BAC, the new regime section) means OLD regime; "No" or the question being absent means NEW regime. Do not guess from the section number alone — the section number appears in the question text regardless of the answer.
  "gross_salary": number,                // Gross Salary total
  "salary_as_per_17_1": number,          // Salary as per section 17(1)
  "perquisites_17_2": number,            // Value of perquisites under section 17(2)
  "profits_17_3": number,                // Profits in lieu of salary under section 17(3)
  "hra_10_13a": number,                  // House rent allowance exemption under section 10(13A)
  "hra_received": number,                // Actual HRA received (0 if not separately stated)
  "lta_10_5": number,                    // Leave travel allowance/concession exemption under section 10(5)
  "other_exempt_10": number,             // Other exempt allowances under section 10(14)
  "total_exempt_10": number,             // Total exemption claimed under section 10
  "standard_deduction_16ia": number,     // Standard deduction under section 16(ia)
  "professional_tax_16iii": number,      // Tax on employment / professional tax under section 16(iii)
  "income_under_salary": number,         // Income chargeable under the head "Salaries"
  "house_property_loss": number,         // Income/loss from house property (negative if a loss)
  "other_sources_income": number,        // Income under the head "Other Sources"
  "sec_80c_claimed": number,             // Deductible amount under section 80C SPECIFICALLY — not the combined "80C, 80CCC and 80CCD(1)" total row
  "sec_80ccd_1_claimed": number,         // Deductible amount under section 80CCD(1) SPECIFICALLY — not the combined total, and not 80CCD(1B) or 80CCD(2)
  "sec_80d_claimed": number,             // Deductible amount under section 80D
  "total_vi_a_claimed": number,          // Aggregate deductible amount under Chapter VI-A
  "taxable_income_form16": number,       // Total taxable income as stated on the form
  "tax_payable_form16": number,          // Tax/net tax payable as stated on the form
  "tds_deducted_form16": number          // Total TDS deducted (Part A summary "Total (Rs.)" under Amount of tax deducted)
}

Rules:
- Distinguish "confirmed zero" from "not found": use 0 ONLY when that line item actually appears on the document and its stated value is zero/nil (this is the common case for inapplicable Chapter VI-A rows etc. — a real Form 16 almost always prints "0.00" for them explicitly, which IS a confirmed zero). Use JSON null when that line item cannot be found anywhere in the document text at all — never use null just because a value is small or a section is inapplicable; inapplicable sections are still usually explicitly printed as 0.00, which is a confirmed zero, not null.
- Never omit a key — every key above must be present, with either a number or null.
- Numbers must be plain numbers: no currency symbols, no commas, no text.
- If employee_name, employee_pan, or assessment_year are genuinely not determinable, use null — never fabricate them.
- Return ONLY the JSON object. No explanation, no markdown code fences."""


def _parse_llm_json(raw: str) -> dict:
    """Strips markdown code fences if present, then parses JSON. Falls back to
    extracting the outermost {...} block if the model added stray text around it."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def extract_form16_llm(full_text: str) -> Form16Data:
    from shared.llm_client import complete_with_system

    print(f"[FORM16-DEBUG] Calling LLM for extraction ({len(full_text)} chars of input text)...", flush=True)
    raw_response = complete_with_system(
        system=FORM16_EXTRACTION_SYSTEM_PROMPT,
        user=f"Form 16 raw extracted text:\n\n{full_text}",
        temperature=0.0,
    )
    print(f"[FORM16-DEBUG] RAW LLM response:\n{raw_response}", flush=True)

    parsed = _parse_llm_json(raw_response)
    print(f"[FORM16-DEBUG] Parsed JSON from LLM:\n{json.dumps(parsed, indent=2, default=str)}", flush=True)

    known_fields = {k: v for k, v in parsed.items() if k in Form16Data.model_fields}
    result = Form16Data(**known_fields)
    result.tax_regime_llm = parsed.get("tax_regime") if parsed.get("tax_regime") in ("old", "new") else None
    result.raw_text_snippet = full_text
    return result


def parse_form16_text(full_text: str) -> Form16Data:
    result = Form16Data()
    result.raw_text_snippet = full_text
    print(f"[FORM16-DEBUG] Starting regex extraction over {len(full_text)} chars", flush=True)
    for field, patterns in {**PART_A_PATTERNS, **PART_B_PATTERNS}.items():
        matched = False
        for pattern in patterns:
            match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
            if match:
                val_str = match.group(1).replace(",", "")
                try:
                    if field in ["employee_name", "employee_pan", "assessment_year"]:
                        setattr(result, field, match.group(1).strip())
                    else:
                        setattr(result, field, float(val_str))
                    matched = True
                    print(f"[FORM16-DEBUG]   {field}: MATCHED pattern {pattern!r} -> {getattr(result, field)!r}", flush=True)
                    break
                except Exception as e:
                    print(f"[FORM16-DEBUG]   {field}: pattern matched but value conversion failed "
                          f"({e}) for raw group={match.group(1)!r}", flush=True)
                    continue
        if not matched:
            print(f"[FORM16-DEBUG]   {field}: NO MATCH — tried {len(patterns)} pattern(s)", flush=True)
    return result

def parse_form16(path: str) -> Form16Data:
    import pdfplumber
    full_text = ""
    with pdfplumber.open(path) as pdf:
        print(f"[FORM16-DEBUG] Opened PDF with {len(pdf.pages)} page(s): {path}", flush=True)
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""
            print(f"[FORM16-DEBUG] --- page {i+1} raw pdfplumber text ({len(page_text)} chars) ---", flush=True)
            print(page_text, flush=True)
            print(f"[FORM16-DEBUG] --- end page {i+1} ---", flush=True)
            full_text += page_text + "\n"
    print(f"[FORM16-DEBUG] TOTAL extracted text length: {len(full_text)} chars", flush=True)
    if len(full_text.strip()) == 0:
        print("[FORM16-DEBUG] WARNING: pdfplumber extracted ZERO text — likely a scanned/image-based "
              "PDF or a text layer pdfplumber can't read. Extraction cannot work on empty text.", flush=True)
        return parse_form16_text(full_text)

    try:
        return extract_form16_llm(full_text)
    except Exception as e:
        print(f"[FORM16-DEBUG] LLM extraction failed ({e!r}) — falling back to regex extraction", flush=True)
        return parse_form16_text(full_text)

def _n(v: Optional[float]) -> float:
    """Null-safe numeric coercion for arithmetic only — never use this to
    decide whether a field was 'found'; check `is None` on the raw
    Form16Data attribute for that instead (see extraction_meta below)."""
    return v if v is not None else 0.0


def form16_to_dict(data: Form16Data) -> dict:
    if data.tax_regime_llm in ("old", "new"):
        # LLM read the actual Yes/No answer to the 115BAC(1A) opt-out question.
        regime = data.tax_regime_llm
    else:
        # Regex fallback path only: crude keyword scan (can't correlate a
        # question with its Yes/No answer, so this is a known weaker signal).
        regime = "new"
        text = data.raw_text_snippet.lower()
        if any(k in text for k in ["old tax regime", "10-iea", "10-ie", "opted out of 115bac"]):
            regime = "old"
        elif any(k in text for k in ["new tax regime", "115bac(1a)", "default regime"]):
            regime = "new"

    # A field is only "missing" if it was truly never found — a fallback sum
    # of sub-components counts as found too, since those sub-components were
    # independently confirmed. Only flag the roll-up itself as missing when
    # BOTH the top-line total AND every component are absent.
    gross_salary_total = data.gross_salary
    if gross_salary_total is None:
        parts = [data.salary_as_per_17_1, data.perquisites_17_2, data.profits_17_3]
        if any(p is not None for p in parts):
            gross_salary_total = sum(_n(p) for p in parts)

    total_exemptions = data.total_exempt_10
    if total_exemptions is None:
        parts = [data.hra_10_13a, data.lta_10_5, data.other_exempt_10]
        if any(p is not None for p in parts):
            total_exemptions = sum(_n(p) for p in parts)

    total_vi_a = data.total_vi_a_claimed
    if total_vi_a is None:
        parts = [data.sec_80c_claimed, data.sec_80d_claimed]
        if any(p is not None for p in parts):
            total_vi_a = sum(_n(p) for p in parts)

    missing_fields = [
        attr for attr in (
            "gross_salary", "hra_10_13a", "professional_tax_16iii",
            "sec_80c_claimed", "sec_80d_claimed", "tds_deducted_form16",
        )
        if getattr(data, attr) is None
    ]

    mapped = {
        "employee_name": data.employee_name,
        "employee_pan": data.employee_pan,
        "assessment_year": data.assessment_year or "2026-27",
        "tax_regime": regime,
        "tds": _n(data.tds_deducted_form16),
        "gross_salary": {
            "salary_17_1":      _n(data.salary_as_per_17_1),
            "perquisites_17_2":  _n(data.perquisites_17_2),
            "profits_17_3":      _n(data.profits_17_3),
            "total":            _n(gross_salary_total),
        },
        "total_exemptions": _n(total_exemptions),
        "hra_exempt": _n(data.hra_10_13a),
        "hra_received": _n(data.hra_received),
        "standard_deduction": data.standard_deduction_16ia if data.standard_deduction_16ia is not None
                               else (75000.0 if regime == "new" else 50000.0),
        "professional_tax": _n(data.professional_tax_16iii),
        "other_income": {
            "house_property": _n(data.house_property_loss),
            "other_sources": _n(data.other_sources_income),
            "total": _n(data.house_property_loss) + _n(data.other_sources_income),
        },
        "chapter_6A": {
            "80C": _n(data.sec_80c_claimed),
            "80D": _n(data.sec_80d_claimed),
            "total": _n(total_vi_a),
        },
        # Not rendered on the ITR-1 form itself — consumed by node_fill_form
        # to build real per-field confidence_scores (source: "missing" vs
        # "form16"), so the UI can show "₹0" for a confirmed zero and "—"
        # only for a field that was genuinely never found.
        "extraction_meta": {"missing_fields": missing_fields},
    }
    print(f"[FORM16-DEBUG] Final form16_to_dict() output:\n{json.dumps(mapped, indent=2, default=str)}", flush=True)
    return mapped
