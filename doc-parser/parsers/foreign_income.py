"""
Foreign Income / Schedule FA Document Parser
================================================
ITR-2 only — parses foreign income and asset disclosure documents into
Schedule FSI (foreign income, for Sec 90/91/90A DTAA relief via
shared.tax_engine.primitives.aggregate_foreign_income/
apply_foreign_tax_credit) and Schedule FA (foreign asset disclosure —
carried through for display, never affects tax computed).

Two extraction paths, same shape as form16.py/capital_gains.py:

  - LLM path (primary): real documents vary far more than "one Form 67
    statement" — a foreign employer's own payroll workbook (month-by-month
    salary in a foreign currency, an exchange rate column, and an already-
    computed INR column) is a completely different shape from a Form 67
    foreign-tax-credit statement, and neither looks like the other. An LLM
    reading the whole document can recognize either shape and, critically,
    knows to SUM a month-by-month INR column into one annual figure rather
    than only reading the first number it sees.
  - Regex path (fallback, when no LLM provider is reachable): the original
    single-country heuristic against one common statement layout.

Currency conversion: this parser does NOT implement Rule 115 (SBI TT
buying-rate lookups) itself. If the source document already states amounts
converted to INR (the common real-world case — a payroll/CA-prepared
workbook has already done this), those are used directly. If a document
only gives a raw foreign-currency amount with no INR conversion anywhere
in it, that entry is dropped with a warning asking for manual entry rather
than guessing an exchange rate — an invented rate would be a worse error
than a missing field.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))  # /app in Docker — for `shared.llm_client`

from parsers._llm_common import parse_llm_json_object


def parse_indian_amount(raw: str) -> float:
    if not raw:
        return 0.0
    clean = re.sub(r"[^\d.]", "", str(raw).replace(",", ""))
    try:
        return float(clean)
    except ValueError:
        return 0.0


def parse_foreign_income_regex(text: str) -> dict:
    result = {
        "doc_type": "foreign_income",
        "foreign_income": [],
        "foreign_assets": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }

    country = None
    country_match = re.search(r"Country[:\s]+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if country_match:
        country = country_match.group(1).strip()

    # The currency marker is mandatory (not optional) — without it, a lazy
    # '.*?' will happily latch onto the nearest unrelated number (a year, a
    # page number) instead of the amount. See property.py for the concrete
    # failure this was found against. DOTALL is also dropped so matches stay
    # within roughly one sentence rather than jumping across the document.
    CURRENCY = r"(?:Rs\.?|INR|₹)"

    income_amount = 0.0
    for pattern in [
        rf"Foreign\s+(?:Source\s+)?Income[:\s]*{CURRENCY}\s*([\d,]+\.?\d*)",
        rf"Income\s+(?:Earned\s+)?(?:Outside\s+India|Abroad)[:\s]*{CURRENCY}\s*([\d,]+\.?\d*)",
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            income_amount = parse_indian_amount(match.group(1))
            break

    tax_paid = 0.0
    tax_match = re.search(
        rf"(?:Foreign\s+)?Tax\s+Paid(?:\s+(?:Abroad|Outside\s+India))?[:\s]*{CURRENCY}\s*([\d,]+\.?\d*)",
        text, re.IGNORECASE,
    )
    if tax_match:
        tax_paid = parse_indian_amount(tax_match.group(1))

    if income_amount > 0 or tax_paid > 0:
        result["foreign_income"].append({
            "country": country,
            "income_type": "other",
            "foreign_income_amount_inr": income_amount,
            "foreign_tax_paid_inr": tax_paid,
        })

    peak_value = 0.0
    peak_match = re.search(rf"Peak\s+(?:Balance|Value)[:\s]*{CURRENCY}\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
    if peak_match:
        peak_value = parse_indian_amount(peak_match.group(1))

    closing_value = 0.0
    closing_match = re.search(rf"Closing\s+(?:Balance|Value)[:\s]*{CURRENCY}\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
    if closing_match:
        closing_value = parse_indian_amount(closing_match.group(1))

    if peak_value > 0 or closing_value > 0:
        result["foreign_assets"].append({
            "country": country,
            "asset_type": "bank_account",
            "peak_value": peak_value,
            "closing_value": closing_value,
        })

    if result["foreign_income"] or result["foreign_assets"]:
        result["parse_confidence"] = 0.5
    else:
        result["warnings"].append(
            "Could not find foreign income/asset figures on this document. "
            "Enter Schedule FA/FSI details manually — disclosure is mandatory "
            "for residents regardless of income earned."
        )

    return result


# ── LLM-based extraction (primary path) ────────────────────────────────────────

class ForeignIncomeEntry(BaseModel):
    country:                  Optional[str] = None
    income_type:               str = "other"   # "salary" | "other" (interest/dividend/rental/other)
    foreign_income_amount_inr: float = 0.0
    foreign_tax_paid_inr:      float = 0.0
    amount_not_convertible:    bool = False   # true if only a foreign-currency figure was found, no INR conversion anywhere in the document


class ForeignAssetEntry(BaseModel):
    country:      Optional[str] = None
    asset_type:   str = "bank_account"   # "bank_account" | "equity" | "property" | "other"
    peak_value_inr:    float = 0.0
    closing_value_inr: float = 0.0


FOREIGN_INCOME_EXTRACTION_SYSTEM_PROMPT = """You are extracting data from a document about a Resident Indian taxpayer's foreign income and/or foreign assets, for ITR-2 Schedule FSI (foreign source income) and Schedule FA (foreign asset disclosure). Documents vary hugely in shape:
- A payroll/CA-prepared workbook with one row per month: a foreign-currency amount, an exchange rate, and an already-computed INR amount — sum the INR column into ONE annual total per income type.
- A Form 67 foreign tax credit statement with a country, income amount, and foreign tax paid already stated in INR (or convertible).
- A foreign bank/brokerage statement showing account balances (for Schedule FA asset disclosure — this is NOT income).

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "foreign_income": [
    {
      "country": string or null,
      "income_type": "salary" or "other",     // "salary" = foreign employment income. "other" = interest, dividends, rental, or anything else — this engine taxes all non-salary foreign income the same way, so do not invent finer categories.
      "foreign_income_amount_inr": number,    // total for this income type/country, ALREADY IN INR. If the document has an explicit "Total" row for the INR column, use that stated total directly — do not re-sum the individual rows yourself even if you could, since a model re-adding many rows from flattened text is more error-prone than reading a total the document already computed. Only sum the individual rows yourself if there is genuinely no total row present.
      "foreign_tax_paid_inr": number,         // total foreign tax paid on this income, in INR, 0 if none stated or none paid
      "amount_not_convertible": boolean       // true ONLY if this document gives a foreign-currency amount with NO INR conversion anywhere (no exchange rate, no INR column) — in that case set foreign_income_amount_inr to 0 rather than guessing a conversion rate
    }
  ],
  "foreign_assets": [
    {
      "country": string or null,
      "asset_type": "bank_account" or "equity" or "property" or "other",
      "peak_value_inr": number,     // highest balance/value during the year, in INR, 0 if not stated
      "closing_value_inr": number   // balance/value at year end, in INR, 0 if not stated
    }
  ]
}

Rules:
- Numbers must be plain numbers: no currency symbols, no commas, no text.
- Never invent or guess an exchange rate to convert a foreign-currency amount to INR yourself — only use an INR figure that the document itself already states or that you can sum from the document's own stated per-row INR values.
- Skip nothing that has real content, but do not invent entries for rows that are entirely zero/blank.
- Return ONLY the JSON object. No explanation, no markdown code fences. If nothing recognizable is found, return {"foreign_income": [], "foreign_assets": []}."""


def extract_foreign_income_llm(text: str) -> tuple[list[ForeignIncomeEntry], list[ForeignAssetEntry]]:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=FOREIGN_INCOME_EXTRACTION_SYSTEM_PROMPT,
        user=f"Foreign income/asset document raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = parse_llm_json_object(raw_response)

    income_entries = []
    for item in parsed.get("foreign_income", []):
        known = {k: v for k, v in item.items() if k in ForeignIncomeEntry.model_fields}
        income_entries.append(ForeignIncomeEntry(**known))

    asset_entries = []
    for item in parsed.get("foreign_assets", []):
        known = {k: v for k, v in item.items() if k in ForeignAssetEntry.model_fields}
        asset_entries.append(ForeignAssetEntry(**known))

    return income_entries, asset_entries


def parse_foreign_income(text: str) -> dict:
    """Top-level entry point: LLM extraction (primary), regex (fallback) —
    same shape as form16.py/capital_gains.py/property.py."""
    if not text or len(text.strip()) < 20:
        return parse_foreign_income_regex(text)

    try:
        income_entries, asset_entries = extract_foreign_income_llm(text)
    except Exception:
        return parse_foreign_income_regex(text)

    result = {
        "doc_type": "foreign_income",
        "foreign_income": [],
        "foreign_assets": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }

    for e in income_entries:
        if e.amount_not_convertible:
            result["warnings"].append(
                f"Found a foreign income figure{f' ({e.country})' if e.country else ''} with no "
                "INR conversion stated anywhere in the document — enter the converted amount "
                "manually rather than relying on a guessed exchange rate."
            )
            continue
        if e.foreign_income_amount_inr <= 0 and e.foreign_tax_paid_inr <= 0:
            continue
        result["foreign_income"].append({
            "country":                   e.country,
            "income_type":               e.income_type if e.income_type in ("salary", "other") else "other",
            "foreign_income_amount_inr": e.foreign_income_amount_inr,
            "foreign_tax_paid_inr":      e.foreign_tax_paid_inr,
        })

    for a in asset_entries:
        if a.peak_value_inr <= 0 and a.closing_value_inr <= 0:
            continue
        result["foreign_assets"].append({
            "country":          a.country,
            "asset_type":       a.asset_type if a.asset_type in ("bank_account", "equity", "property", "other") else "other",
            "peak_value":       a.peak_value_inr,
            "closing_value":    a.closing_value_inr,
        })

    if result["foreign_income"] or result["foreign_assets"]:
        result["parse_confidence"] = 0.75
        return result

    if result["warnings"]:
        return result
    return parse_foreign_income_regex(text)
