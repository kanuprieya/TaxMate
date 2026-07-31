"""
Foreign Income / Schedule FA Document Parser
================================================
ITR-2 only — parses foreign income and asset disclosure documents (e.g. a
Form 67 foreign tax credit statement, or a foreign employer/broker
statement) into Schedule FSI (foreign income, for Sec 90/91 DTAA relief) and
Schedule FA (foreign asset disclosure) entries.

First-cut heuristic parser against one common statement layout — not a
parser for every country/institution's document format.
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


def parse_foreign_income(text: str) -> dict:
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
            "foreign_income_amount": income_amount,
            "foreign_tax_paid": tax_paid,
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
