"""
Bank Statement Parser
======================
Uses pdfplumber to extract tables and classifies transactions.
Focuses on salary credits and interest income.
"""

import pdfplumber
import re
from typing import Optional

def parse_indian_amount(raw: str) -> float:
    if not raw: return 0.0
    # Handle cases like "1,23,456.00 CR" or "DR"
    clean = re.sub(r'[^\d.]', '', str(raw).replace(',', ''))
    try:
        return float(clean)
    except ValueError:
        return 0.0

BANK_CREDIT_KEYWORDS = [
    "salary", "sal", "neft", "imps", "credit", "interest",
    "dividend", "refund", "crdr"
]

def _empty_result() -> dict:
    return {
        "doc_type": "BANKSTMT",
        "salary_credits": [],
        "interest_credits": [],
        "total_credits": 0.0,
        "total_debits": 0.0,
        "transactions": [],
        "total_salary": 0.0,
        "total_interest": 0.0,
        "parse_confidence": 0.0,
        "warnings": []
    }


def _classify_rows(rows: list) -> dict:
    """Row-classification core, format-agnostic: a "row" is just a list of
    cell values, whether it came from a pdfplumber table or an Excel sheet
    (bank statement downloads — passbook exports especially — are naturally
    already tabular, so no separate text-flattening step is needed here
    the way form16/capital_gains/property/foreign_income need for .xlsx)."""
    result = _empty_result()

    for row in rows:
        if not row or not any(row):
            continue

        row_text = " ".join(
            str(cell) for cell in row if cell
        ).lower()

        # Identify amount columns
        # Most banks: [Date, Description, Debit, Credit, Balance]
        amounts_raw = re.findall(r"[\d,]+\.\d{2}", row_text)
        if not amounts_raw:
            amounts_raw = re.findall(r"[\d,]+", row_text)

        amounts = [parse_indian_amount(a) for a in amounts_raw if parse_indian_amount(a) > 0]

        if any(kw in row_text for kw in BANK_CREDIT_KEYWORDS):
            if amounts:
                # In a bank statement row, the larger of the two numbers (debit/credit)
                # is usually the one we want if it's a credit keyword row.
                # Or if it's the 4th column in a 5-column table.
                credit_amount = max(amounts)

                result["transactions"].append({
                    "description": row_text,
                    "amount": credit_amount,
                    "type": "credit"
                })

                if any(sk in row_text for sk in ["salary", "sal"]):
                    result["salary_credits"].append(credit_amount)
                elif "interest" in row_text:
                    result["interest_credits"].append(credit_amount)

    result["total_salary"] = sum(result["salary_credits"])
    result["total_interest"] = sum(result["interest_credits"])

    if result["total_salary"] > 0 or result["total_interest"] > 0:
        result["parse_confidence"] = 0.8
    else:
        result["parse_confidence"] = 0.3

    return result


def parse_bank_statement_from_rows(rows: list) -> dict:
    """Entry point for any source that already has row/cell data — used by
    parse_bank_statement() below (pdfplumber tables) and, from
    doc-parser/main.py, for .xlsx uploads (openpyxl rows)."""
    try:
        return _classify_rows(rows)
    except Exception as e:
        result = _empty_result()
        result["warnings"].append(f"Bank statement parse error: {str(e)}")
        return result


def parse_bank_statement(pdf_path: str) -> dict:
    try:
        rows = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    rows.extend(table)
        return parse_bank_statement_from_rows(rows)
    except Exception as e:
        result = _empty_result()
        result["warnings"].append(f"Bank statement parse error: {str(e)}")
        return result
