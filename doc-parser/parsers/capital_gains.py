"""
Capital Gains Statement Parser
=================================
ITR-2 only — parses broker/RTA capital gains statements (the summary most
brokers issue for tax filing, e.g. Zerodha/Groww/ICICI Direct/CAMS "Capital
Gains Statement").

Two extraction paths, same shape as doc-parser/parsers/form16.py:

  - LLM path (primary): broker statements vary wildly in layout — some print
    only the 4-way short/long x equity/other summary, others print a full
    scrip-wise transaction table with real buy/sell values and dates. An LLM
    reading the whole document can recover per-transaction cost_of_acquisition
    when it's actually present, instead of always collapsing to the summary
    heuristic below. Returns the same CAPITAL_GAINS_PATTERNS-shaped list of
    transaction dicts the regex path produces, so node_fill_form's
    _merge_docs("capital_gains", "capital_gains_raw") doesn't need to care
    which path produced them.
  - Regex path (fallback, when no LLM provider is reachable): looks for the
    4-way summary and synthesizes one pseudo-transaction per non-zero bucket.
    asset_type + a holding period safely on the correct side of the
    equity(12mo)/other(24mo) threshold is enough for compute_capital_gains to
    classify it into the right bucket, even though only the net gain was
    ever known (cost_of_acquisition left at 0, full gain carried as
    sale_value — a documented simplification, not a real buy/sell
    reconstruction). First-cut heuristic against one common statement
    layout, same spirit as doc-parser/parsers/ais.py's SFT-pattern matching —
    not a parser for every broker's PDF format.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))  # /app in Docker — for `shared.llm_client`


def _months_between(acquisition_date: Optional[str], sale_date: Optional[str]) -> Optional[float]:
    """shared.tax_engine's compute_capital_gains only ever reads
    holding_period_months (never the raw dates) to decide short vs long
    term, so if the LLM gave us real dates but skipped the arithmetic (or
    got it wrong), recompute it deterministically here rather than trust a
    model's date-math on a field this load-bearing."""
    if not acquisition_date or not sale_date:
        return None
    try:
        y1, m1, d1 = (int(p) for p in acquisition_date.split("-"))
        y2, m2, d2 = (int(p) for p in sale_date.split("-"))
        acq, sale = date(y1, m1, d1), date(y2, m2, d2)
    except (ValueError, TypeError):
        return None
    months = (sale.year - acq.year) * 12 + (sale.month - acq.month)
    if sale.day < acq.day:
        months -= 1
    return float(max(months, 0))


def parse_indian_amount(raw: str) -> float:
    if not raw:
        return 0.0
    clean = re.sub(r"[^\d.]", "", str(raw).replace(",", ""))
    try:
        return float(clean)
    except ValueError:
        return 0.0


# The currency marker is mandatory (not optional) — without it, a lazy
# '.*?' will happily latch onto the nearest unrelated number (a year, a page
# number) instead of the amount. See property.py for the concrete failure
# this was found against.
CURRENCY = r"(?:Rs\.?|INR|₹)"

CAPITAL_GAINS_PATTERNS = {
    # (asset_type, holding_period_months placed safely on one side of the
    # threshold used by compute_capital_gains's default params)
    "stcg_111a":      (rf"Short[\s-]*Term.*?Equity.*?{CURRENCY}\s*([\d,]+\.?\d*)", "equity_stt", 6),
    "ltcg_112a":       (rf"Long[\s-]*Term.*?Equity.*?{CURRENCY}\s*([\d,]+\.?\d*)", "equity_stt", 13),
    "stcg_other":      (rf"Short[\s-]*Term.*?(?:Debt|Other|Non-?Equity).*?{CURRENCY}\s*([\d,]+\.?\d*)", "other", 6),
    "ltcg_112_other":  (rf"Long[\s-]*Term.*?(?:Debt|Other|Non-?Equity).*?{CURRENCY}\s*([\d,]+\.?\d*)", "other", 30),
}


def parse_capital_gains_regex(text: str) -> dict:
    result = {
        "doc_type": "capital_gains",
        "capital_gains_raw": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }

    for bucket, (pattern, asset_type, holding_months) in CAPITAL_GAINS_PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            gain = parse_indian_amount(match.group(1))
            if gain > 0:
                result["capital_gains_raw"].append({
                    "asset_type": asset_type,
                    "description": bucket,
                    "holding_period_months": holding_months,
                    "sale_value": gain,
                    "cost_of_acquisition": 0.0,
                })

    if result["capital_gains_raw"]:
        result["parse_confidence"] = 0.6
    else:
        result["warnings"].append(
            "Could not find a recognizable short/long-term equity/other capital gains "
            "summary in this document. Enter capital gains manually."
        )

    return result


# ── LLM-based extraction (primary path) ────────────────────────────────────────
# Same rationale as form16.py: layouts vary by broker/RTA (Zerodha, Groww,
# ICICI Direct, CAMS, ...), and some print a full scrip-wise transaction
# table rather than just the 4-way summary the regex path above looks for.
# An LLM reading the whole document can pull real per-transaction buy/sell
# values out of that table when present, instead of always collapsing to
# the summary heuristic (cost_of_acquisition=0). When only a summary is
# present, it still produces the same shape the regex path would.

class CapitalGainsTransaction(BaseModel):
    asset_type:            str              # "equity_stt" | "other"
    description:           Optional[str] = None
    holding_period_months: Optional[float] = None
    acquisition_date:      Optional[str] = None   # YYYY-MM-DD, if stated
    sale_date:              Optional[str] = None   # YYYY-MM-DD, if stated
    sale_value:             Optional[float] = None
    cost_of_acquisition:    Optional[float] = None


CAPITAL_GAINS_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data from an Indian broker/RTA Capital Gains Statement (e.g. Zerodha, Groww, ICICI Direct, CAMS) for ITR-2 Schedule CG. The text was extracted with pdfplumber and may have jumbled column order or merged lines — read the whole document and use context to find the right values, don't just pattern-match nearby text.

Return ONLY a single valid JSON array (no markdown fences, no commentary) of transaction objects. Each object has exactly these keys:

{
  "asset_type": "equity_stt" or "other",   // "equity_stt" = listed equity shares / equity-oriented mutual funds / business trust units where STT was paid (Sec 111A/112A). "other" = every other capital asset (Sec 112) — debt funds, unlisted shares, property, gold, bonds, etc.
  "description": string,                   // scrip/fund name or a short label for the bucket (e.g. "Equity — Short Term")
  "holding_period_months": number or null, // if the document doesn't give exact dates but does classify as short/long term, use a value safely on the correct side of the threshold: equity short-term < 12, equity long-term >= 12; other short-term < 24, other long-term >= 24
  "acquisition_date": string or null,      // "YYYY-MM-DD" if stated, else null
  "sale_date": string or null,             // "YYYY-MM-DD" if stated, else null
  "sale_value": number,                    // total sale consideration for this transaction/bucket
  "cost_of_acquisition": number            // total cost of acquisition (+ improvement cost if stated) for this transaction/bucket — use 0 ONLY if the document only gives a net gain/loss figure with no separate buy/sell breakdown, never guess a cost basis that isn't in the document
}

Rules:
- If the document lists individual scrip-wise transactions (a real transaction table), return one object per transaction with real sale_value and cost_of_acquisition from that table — do not collapse them into a summary.
- If the document only shows the aggregate 4-way summary (short/long term x equity/other), return one object per non-zero bucket, with sale_value equal to the stated net gain and cost_of_acquisition 0 (there is no way to recover a cost basis that was never printed).
- Skip buckets/rows that are zero, blank, or not present — do not invent transactions.
- Numbers must be plain numbers: no currency symbols, no commas, no text.
- Return ONLY the JSON array. No explanation, no markdown code fences. If nothing recognizable is found, return an empty array []."""


def _parse_llm_json_array(raw: str) -> list:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def extract_capital_gains_llm(text: str) -> list[CapitalGainsTransaction]:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=CAPITAL_GAINS_EXTRACTION_SYSTEM_PROMPT,
        user=f"Capital gains statement raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = _parse_llm_json_array(raw_response)

    transactions = []
    for item in parsed:
        known = {k: v for k, v in item.items() if k in CapitalGainsTransaction.model_fields}
        transactions.append(CapitalGainsTransaction(**known))
    return transactions


def parse_capital_gains(text: str) -> dict:
    """Top-level entry point: LLM extraction (primary), regex (fallback) —
    same shape as form16.py's parse_form16(). Output shape matches
    parse_capital_gains_regex() exactly, since node_fill_form's _merge_docs
    doesn't distinguish which path produced a given capital_gains_raw entry."""
    if not text or len(text.strip()) < 50:
        return parse_capital_gains_regex(text)

    try:
        transactions = extract_capital_gains_llm(text)
    except Exception:
        return parse_capital_gains_regex(text)

    result = {
        "doc_type": "capital_gains",
        "capital_gains_raw": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }
    for t in transactions:
        if not t.sale_value or t.sale_value <= 0:
            continue
        holding_months = t.holding_period_months
        computed = _months_between(t.acquisition_date, t.sale_date)
        if computed is not None:
            holding_months = computed  # dates, when present, are ground truth over the LLM's own arithmetic
        result["capital_gains_raw"].append({
            "asset_type":            t.asset_type if t.asset_type in ("equity_stt", "other") else "other",
            "description":           t.description,
            "acquisition_date":      t.acquisition_date,
            "sale_date":             t.sale_date,
            "holding_period_months": holding_months if holding_months is not None else 0.0,
            "sale_value":            t.sale_value,
            "cost_of_acquisition":   t.cost_of_acquisition or 0.0,
        })

    if result["capital_gains_raw"]:
        # LLM path with a genuine per-transaction cost basis (not the summary
        # heuristic's cost_of_acquisition=0) is a stronger signal than the
        # regex path's fixed 0.6 — reflect that, but stay short of 1.0 since
        # this is still first-pass extraction, not a verified statement.
        has_cost_basis = any(c["cost_of_acquisition"] > 0 for c in result["capital_gains_raw"])
        result["parse_confidence"] = 0.75 if has_cost_basis else 0.6
    else:
        return parse_capital_gains_regex(text)

    return result
