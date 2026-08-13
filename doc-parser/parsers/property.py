"""
House Property Document Parser
==================================
ITR-2 only — parses a single property's supporting document into one or
more Schedule HP entries. A filer with multiple properties uploads one
document per property (or one combined rent-roll/summary covering several);
agent-orchestrator/graph/itr2_graph.py merges all of them together before
handing them to shared.tax_engine's aggregate_house_properties primitive.

Two extraction paths, same shape as form16.py/capital_gains.py:

  - LLM path (primary): real documents vary far more than "one bank's home-
    loan interest certificate" — a multi-tenant rent-roll summary lists
    several properties' rent totals with no "Interest Paid"/"Annual Value"
    label at all (found against a real rent-roll spreadsheet that the
    regex-only version below could not read at all). An LLM reading the
    whole document can return one entry per property when several are
    listed, and infer annual_value from total rent received when the
    document doesn't use that exact term.
  - Regex path (fallback, when no LLM provider is reachable): the original
    single-property heuristic against one common bank-certificate layout.
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


def parse_property_regex(text: str) -> dict:
    result = {
        "doc_type": "property",
        "house_properties": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }

    address = None
    addr_match = re.search(r"Property\s+Address[:\s]+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if addr_match:
        address = addr_match.group(1).strip()

    # The currency marker is mandatory (not optional) in every pattern below —
    # without it, a lazy '.*?' will happily latch onto the nearest unrelated
    # number instead (a year in "Financial Year 2025-26", a page number,
    # etc). Found via testing against a realistic certificate: "Interest
    # Paid ... Financial Year 2025-26: Rs 50,000" matched "2025" instead of
    # "50,000" until the marker was made mandatory.
    CURRENCY = r"(?:Rs\.?|INR|₹)"

    interest = 0.0
    for pattern in [
        rf"Interest\s+(?:Paid|Charged)\b.*?{CURRENCY}\s*([\d,]+\.?\d*)",
        rf"Total\s+Interest.*?{CURRENCY}\s*([\d,]+\.?\d*)",
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            interest = parse_indian_amount(match.group(1))
            break

    annual_value = 0.0
    av_match = re.search(rf"(?:Annual|Gross)\s+(?:Rent|Value)\s*(?:Received)?[:\s]*{CURRENCY}\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
    if av_match:
        annual_value = parse_indian_amount(av_match.group(1))

    municipal_tax = 0.0
    mt_match = re.search(rf"Municipal\s+Tax(?:es)?.*?{CURRENCY}\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
    if mt_match:
        municipal_tax = parse_indian_amount(mt_match.group(1))

    text_lower = text.lower()
    if "let out" in text_lower or "let-out" in text_lower or "rent" in text_lower:
        property_type = "let_out"
    else:
        property_type = "self_occupied"

    if interest > 0 or annual_value > 0:
        result["house_properties"].append({
            "address": address,
            "property_type": property_type,
            "annual_value": annual_value,
            "municipal_tax_paid": municipal_tax,
            "interest_on_loan_24b": interest,
        })
        result["parse_confidence"] = 0.6
    else:
        result["warnings"].append(
            "Could not find a recognizable interest certificate or rent summary in this "
            "document. Enter house property details manually."
        )

    return result


# ── LLM-based extraction (primary path) ────────────────────────────────────────

class HousePropertyEntry(BaseModel):
    address:               Optional[str] = None
    property_type:         str = "self_occupied"   # "self_occupied" | "let_out"
    annual_value:           float = 0.0
    municipal_tax_paid:     float = 0.0
    interest_on_loan_24b:   float = 0.0


PROPERTY_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data from an Indian house-property income document for ITR-2 Schedule HP. This could be a bank's home-loan interest certificate for ONE property, OR a rent-roll/summary listing rent received across MULTIPLE properties/tenants — read the whole document and return one entry per distinct property.

Return ONLY a single valid JSON array (no markdown fences, no commentary) of property objects. Each object has exactly these keys:

{
  "address": string or null,             // property address, if stated
  "property_type": "self_occupied" or "let_out",   // "let_out" if any rent is received on this property, "self_occupied" if the document indicates the owner lives there or no rent is mentioned
  "annual_value": number,                // for a let-out property: total rent received/receivable for the year (sum across tenants if more than one for the same property). For self-occupied: 0, unless the document states a deemed/notional rental value
  "municipal_tax_paid": number,          // municipal/property tax paid this year, 0 if not stated
  "interest_on_loan_24b": number         // home loan interest paid this year (Section 24(b)), 0 if not stated or not applicable
}

Rules:
- If the document lists multiple properties (e.g. a rent roll with several tenants at different addresses), return one object PER PROPERTY, summing rent across tenants at the same address into that property's annual_value.
- If it's a single home-loan interest certificate for one property, return a single-item array.
- Numbers must be plain numbers: no currency symbols, no commas, no text.
- Skip nothing — if a property has rent but the interest certificate wasn't part of this document, still include it with interest_on_loan_24b: 0.
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


def extract_property_llm(text: str) -> list[HousePropertyEntry]:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=PROPERTY_EXTRACTION_SYSTEM_PROMPT,
        user=f"House property document raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = _parse_llm_json_array(raw_response)

    entries = []
    for item in parsed:
        known = {k: v for k, v in item.items() if k in HousePropertyEntry.model_fields}
        entries.append(HousePropertyEntry(**known))
    return entries


def parse_property(text: str) -> dict:
    """Top-level entry point: LLM extraction (primary), regex (fallback) —
    same shape as form16.py/capital_gains.py's parse_form16()/
    parse_capital_gains()."""
    if not text or len(text.strip()) < 20:
        return parse_property_regex(text)

    try:
        entries = extract_property_llm(text)
    except Exception:
        return parse_property_regex(text)

    result = {
        "doc_type": "property",
        "house_properties": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }
    for e in entries:
        if e.annual_value <= 0 and e.interest_on_loan_24b <= 0:
            continue
        result["house_properties"].append({
            "address":               e.address,
            "property_type":         e.property_type if e.property_type in ("self_occupied", "let_out") else "self_occupied",
            "annual_value":          e.annual_value,
            "municipal_tax_paid":    e.municipal_tax_paid,
            "interest_on_loan_24b":  e.interest_on_loan_24b,
        })

    if result["house_properties"]:
        result["parse_confidence"] = 0.75
        return result
    return parse_property_regex(text)
