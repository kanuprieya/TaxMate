"""
Home Loan Statement Parser
=============================
ITR-2 — Section 80C (principal repayment only). Interest u/s 24(b) is
already captured per-property by property.py's Interest Certificate path;
this parser extracts principal_repaid for 80C and surfaces interest_paid
only as a cross-check field (agent-orchestrator/graph/itr2_graph.py must
never add interest_paid into the tax computation itself — that would
double-count against whatever property.py already reported for the same
loan).
"""

import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers._llm_common import parse_llm_json_object


class HomeLoanData(BaseModel):
    principal_repaid: float = 0.0
    interest_paid: float = 0.0
    lender_name: Optional[str] = None


HOME_LOAN_SYSTEM_PROMPT = """You are extracting data from an Indian home loan repayment/interest certificate for ITR-2. Banks typically print BOTH the principal repaid and the interest paid for the financial year on the same certificate.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "principal_repaid": number,   // total principal repaid this financial year (Section 80C) — 0 if not stated
  "interest_paid": number,      // total interest paid this financial year (Section 24(b)) — 0 if not stated
  "lender_name": string or null // name of the bank/lender, if stated
}

Rules:
- Both numbers must be plain numbers: no currency symbols, no commas.
- If the certificate only shows a combined EMI total with no principal/interest split, leave both as 0 — do not guess a split.
- Return ONLY the JSON object. No explanation, no markdown code fences."""


def extract_home_loan_llm(text: str) -> HomeLoanData:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=HOME_LOAN_SYSTEM_PROMPT,
        user=f"Home loan statement raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = parse_llm_json_object(raw_response)
    known = {k: v for k, v in parsed.items() if k in HomeLoanData.model_fields}
    return HomeLoanData(**known)


def parse_home_loan(text: str) -> dict:
    result = {
        "doc_type": "home_loan",
        "principal_repaid": 0.0,
        "interest_paid": 0.0,
        "parse_confidence": 0.0,
        "warnings": [],
    }
    if not text or len(text.strip()) < 20:
        result["warnings"].append("Document appears empty — could not extract loan figures.")
        return result

    try:
        data = extract_home_loan_llm(text)
    except Exception as e:
        result["warnings"].append(
            f"Could not extract principal/interest figures from this document ({e}). "
            "Enter them manually."
        )
        return result

    if data.principal_repaid <= 0 and data.interest_paid <= 0:
        result["warnings"].append(
            "Could not find principal or interest figures on this document. Enter them manually."
        )
        return result

    result["principal_repaid"] = data.principal_repaid
    result["interest_paid"] = data.interest_paid
    result["lender_name"] = data.lender_name
    result["parse_confidence"] = 0.75
    if data.principal_repaid <= 0:
        result["warnings"].append(
            "Found interest paid but no principal repayment figure — 80C claim from this "
            "document will be Rs 0. Enter principal manually if it was actually repaid."
        )
    return result
