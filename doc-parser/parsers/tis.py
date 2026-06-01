"""
TIS (Taxpayer Information Summary) Parser
===========================================
TIS is a simplified version of AIS, usually 1-2 pages.
It has 3 main columns: Category | Reported Value | Accepted by Taxpayer.
Always use the 'Accepted' column as the ground truth.
"""

import re
from typing import Optional

def parse_indian_amount(raw: str) -> float:
    if not raw: return 0.0
    # Remove currency symbols, commas, and whitespace
    clean = re.sub(r'[^\d.]', '', str(raw).replace(',', ''))
    try:
        return float(clean)
    except ValueError:
        return 0.0

def parse_tis(text: str) -> dict:
    result = {
        "doc_type": "TIS",
        "assessment_year": None,
        "salary": 0.0,
        "savings_interest": 0.0,
        "fd_interest": 0.0,
        "dividend": 0.0,
        "rent_received": 0.0,
        "capital_gains": 0.0,
        "other_income": 0.0,
        "parse_confidence": 0.0,
        "warnings": []
    }

    # AY detection
    ay_match = re.search(r"Assessment Year[:\s]+(20\d\d-\d\d)", text, re.IGNORECASE)
    if ay_match:
        result["assessment_year"] = f"AY{ay_match.group(1)}"
    else:
        # Fallback regex if label is missing
        ay_match = re.search(r"(20\d\d-\d\d)", text)
        if ay_match:
            result["assessment_year"] = f"AY{ay_match.group(1)}"

    # Each income category — TIS has 3 columns:
    # Category | Reported Value | Accepted by Taxpayer
    # Always use ACCEPTED column (last column) as authoritative
    # Pattern: Category Name ... [Reported] ... [Accepted]
    
    income_categories = {
        "salary":            r"Salary\s+([\d,]+)\s+([\d,]+)",
        "savings_interest":  r"Interest from savings bank\s+([\d,]+)\s+([\d,]+)",
        "fd_interest":       r"Interest from deposit\s+([\d,]+)\s+([\d,]+)",
        "dividend":          r"Dividend\s+([\d,]+)\s+([\d,]+)",
        "rent_received":     r"Rent received\s+([\d,]+)\s+([\d,]+)",
        "capital_gains":     r"Capital [Gg]ains\s+([\d,]+)\s+([\d,]+)",
        "other_income":      r"Other income\s+([\d,]+)\s+([\d,]+)",
    }

    match_count = 0
    for field, pattern in income_categories.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # group(2) = accepted value = authoritative
            val = parse_indian_amount(match.group(2))
            if val > 0:
                result[field] = val
                match_count += 1

    if match_count > 0:
        result["parse_confidence"] = 0.9
    elif result["assessment_year"]:
        result["parse_confidence"] = 0.5
        
    return result
