"""
AIS (Annual Information Statement) Parser
===========================================
AIS contains information on all financial transactions.
Extracts salary, TDS, interest, and capital gains.
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

def parse_ais(text: str) -> dict:
    result = {
        "doc_type": "AIS",
        "assessment_year": None,
        "gross_salary": 0.0,
        "tds_salary": 0.0,
        "savings_interest": 0.0,
        "fd_interest": 0.0,
        "capital_gains": 0.0,
        "parse_confidence": 0.0,
        "warnings": []
    }

    # AY
    ay_match = re.search(r"Assessment Year[:\s]+(20\d\d-\d\d)", text, re.IGNORECASE)
    if ay_match:
        result["assessment_year"] = f"AY{ay_match.group(1)}"
    else:
        ay_match = re.search(r"(20\d\d-\d\d)", text)
        if ay_match:
            result["assessment_year"] = f"AY{ay_match.group(1)}"

    # Salary
    for pattern in [
        r"Gross Salary.*?17\s*\(1\)\s*[\n\r\s]*([\d,]+)",
        r"Salary.*?Modified Value\s*([\d,]+)",
        r"Salary.*?Reported Value\s*([\d,]+)",
    ]:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            result["gross_salary"] = parse_indian_amount(match.group(1))
            break

    # TDS from employer
    match = re.search(
        r"TDS.*?Deducted\s+([\d,]+\.?\d*)", 
        text, re.IGNORECASE
    )
    if match:
        result["tds_salary"] = parse_indian_amount(match.group(1))

    # Savings interest — SFT-016 entries
    savings_matches = re.findall(
        r"SFT-016.*?([\d,]+)", text, re.IGNORECASE
    )
    if savings_matches:
        result["savings_interest"] = sum(
            parse_indian_amount(x) for x in savings_matches
        )

    # FD interest — SFT-015 entries
    fd_matches = re.findall(
        r"SFT-015.*?([\d,]+)", text, re.IGNORECASE
    )
    if fd_matches:
        result["fd_interest"] = sum(
            parse_indian_amount(x) for x in fd_matches
        )

    # Capital gains — SFT-018 entries
    cg_matches = re.findall(
        r"SFT-018.*?([\d,]+)", text, re.IGNORECASE
    )
    if cg_matches:
        result["capital_gains"] = sum(
            parse_indian_amount(x) for x in cg_matches
        )

    if any([result["gross_salary"], result["savings_interest"], result["fd_interest"]]):
        result["parse_confidence"] = 0.85
    elif result["assessment_year"]:
        result["parse_confidence"] = 0.4

    return result
