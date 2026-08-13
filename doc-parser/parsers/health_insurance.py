"""
Health Insurance Premium Certificate Parser
==============================================
ITR-2 (and old-regime-only) — Section 80D. LLM-only, no regex fallback:
insurer premium certificates vary too much in layout for a narrow regex to
be worth maintaining (same lesson as capital_gains.py/form16.py, applied
from the start here instead of discovered the hard way).
"""

import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers._llm_common import parse_llm_json_object


class HealthInsuranceData(BaseModel):
    premium_paid: float = 0.0
    covers_parents: bool = False
    parents_senior_citizen: bool = False
    insurer_name: Optional[str] = None


HEALTH_INSURANCE_SYSTEM_PROMPT = """You are extracting data from an Indian health insurance premium payment certificate/receipt for ITR-2 Section 80D.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "premium_paid": number,              // total premium paid this financial year (sum across receipts if more than one is shown)
  "covers_parents": boolean,           // true if this policy is for the taxpayer's parents (separate 80D limit from self/family), false if for self/spouse/children
  "parents_senior_citizen": boolean,   // true only if the document states the insured parent(s) are senior citizens (60+) — affects the deduction cap. false if not stated or not applicable
  "insurer_name": string or null       // name of the insurance company, if stated
}

Rules:
- premium_paid must be a plain number: no currency symbols, no commas.
- If multiple premium line items are shown (e.g. base + top-up), sum them into one premium_paid figure.
- Use false for parents_senior_citizen unless the document explicitly says so — never guess.
- Return ONLY the JSON object. No explanation, no markdown code fences."""


def extract_health_insurance_llm(text: str) -> HealthInsuranceData:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=HEALTH_INSURANCE_SYSTEM_PROMPT,
        user=f"Health insurance premium document raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = parse_llm_json_object(raw_response)
    known = {k: v for k, v in parsed.items() if k in HealthInsuranceData.model_fields}
    return HealthInsuranceData(**known)


def parse_health_insurance(text: str) -> dict:
    result = {
        "doc_type": "health_insurance",
        "premium_paid": 0.0,
        "covers_parents": False,
        "parents_senior_citizen": False,
        "parse_confidence": 0.0,
        "warnings": [],
    }
    if not text or len(text.strip()) < 20:
        result["warnings"].append("Document appears empty — could not extract premium amount.")
        return result

    try:
        data = extract_health_insurance_llm(text)
    except Exception as e:
        result["warnings"].append(
            f"Could not extract a health insurance premium figure from this document ({e}). "
            "Enter the amount manually."
        )
        return result

    if data.premium_paid <= 0:
        result["warnings"].append(
            "Could not find a premium amount on this document. Enter it manually."
        )
        return result

    result["premium_paid"] = data.premium_paid
    result["covers_parents"] = data.covers_parents
    result["parents_senior_citizen"] = data.parents_senior_citizen
    result["insurer_name"] = data.insurer_name
    result["parse_confidence"] = 0.75
    return result
