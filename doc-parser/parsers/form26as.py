"""
Form 26AS Parser
=================
Extracts TDS, salary, and interest income from Form 26AS PDF.
Form 26AS is the authoritative source for total TDS credit.
"""

import re
from typing import Optional

def parse_indian_amount(raw: str) -> float:
    if not raw: return 0.0
    clean = re.sub(r'[^\d.]', '', str(raw).replace(',', ''))
    try:
        return float(clean)
    except ValueError:
        return 0.0

FORM26AS_PATTERNS = {
    "assessment_year": [
        r"Assessment Year\s*[\n\r:]+\s*(20\d\d-\d\d)",
        r"A\.Y\.\s*(20\d\d-\d\d)",
    ],
    "employer_name": [
        r"Name of Deductor\s*[\n\r]+\s*(.+?)(?:\n|TAN|$)",
    ],
    "employer_tan": [
        r"TAN of Deductor\s*[\n\r:]+\s*([A-Z]{4}\d{5}[A-Z])",
    ],
    "total_salary_paid": [
        r"Total Amount Paid.*?Credited\s*#?\s*([\d,]+\.?\d*)",
        r"192[A-Z\s]+.*?([\d,]+\.?\d*)\s+[\d,]+\.?\d*\s+[\d,]+",
    ],
    "total_tds_deposited": [
        r"Total TDS.*?Deposited\s*#?\s*([\d,]+\.?\d*)",
        r"Total Tax Deducted\s*#?\s*([\d,]+\.?\d*)",
    ],
}

def parse_form26as(text: str) -> dict:
    result = {
        "doc_type": "FORM26AS",
        "assessment_year": None,
        "employer_name": None,
        "employer_tan": None,
        "total_salary_paid": 0.0,
        "total_tds_deposited": 0.0,
        "total_tds_all_sources": 0.0,
        "parse_confidence": 0.0,
        "warnings": []
    }

    for field, patterns in FORM26AS_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                raw = match.group(1).strip()
                if field in ["total_salary_paid", "total_tds_deposited"]:
                    result[field] = parse_indian_amount(raw)
                else:
                    if field == "assessment_year":
                        result[field] = f"AY{raw}"
                    else:
                        result[field] = raw
                break

    # Sum ALL TDS entries across all sections
    # 26AS is authoritative for total TDS
    # Patterns for rows: Section | Amount Paid | Tax Deducted | Tax Deposited
    all_tds = re.findall(
        r"(?:192|194[A-Z]*|195)\s+.*?([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)",
        text, re.IGNORECASE
    )
    if all_tds:
        # Third column is usually TDS deposited
        result["total_tds_all_sources"] = sum(
            parse_indian_amount(row[2]) for row in all_tds
        )
        result["parse_confidence"] = 0.95
    elif result["total_tds_deposited"] > 0:
        result["total_tds_all_sources"] = result["total_tds_deposited"]
        result["parse_confidence"] = 0.8
    elif result["assessment_year"]:
        result["parse_confidence"] = 0.5

    return result
