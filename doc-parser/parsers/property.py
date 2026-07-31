"""
House Property Document Parser
==================================
ITR-2 only — parses a single property's supporting document (typically a
bank's annual home-loan interest certificate, e.g. "Interest Certificate for
the Financial Year", plus rent receipts for let-out properties) into one
Schedule HP entry. A filer with multiple properties uploads one document per
property; agent-orchestrator/graph/itr2_graph.py merges all of them together
before handing them to shared.tax_engine's aggregate_house_properties
primitive.

First-cut heuristic parser against one common certificate layout — not a
parser for every bank's format.
"""

import re


def parse_indian_amount(raw: str) -> float:
    if not raw:
        return 0.0
    clean = re.sub(r"[^\d.]", "", str(raw).replace(",", ""))
    try:
        return float(clean)
    except ValueError:
        return 0.0


def parse_property(text: str) -> dict:
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
            "Could not find interest/annual value figures on this document. "
            "Enter house property details manually."
        )

    return result
