"""
Life Insurance Premium Certificate Parser
=============================================
ITR-2 (and old-regime-only) — Section 80C. LLM-only, same rationale as
health_insurance.py: insurer premium receipts vary too much for regex.
"""

import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers._llm_common import parse_llm_json_object


class LifeInsuranceData(BaseModel):
    premium_paid: float = 0.0
    insurer_name: Optional[str] = None


LIFE_INSURANCE_SYSTEM_PROMPT = """You are extracting data from an Indian life insurance premium payment certificate/receipt for ITR-2 Section 80C.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "premium_paid": number,     // total premium paid this financial year (sum across receipts/policies if more than one is shown)
  "insurer_name": string or null   // name of the insurance company, if stated
}

Rules:
- premium_paid must be a plain number: no currency symbols, no commas.
- If multiple policies/receipts are shown, sum them into one premium_paid figure.
- Return ONLY the JSON object. No explanation, no markdown code fences."""


def extract_life_insurance_llm(text: str) -> LifeInsuranceData:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=LIFE_INSURANCE_SYSTEM_PROMPT,
        user=f"Life insurance premium document raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = parse_llm_json_object(raw_response)
    known = {k: v for k, v in parsed.items() if k in LifeInsuranceData.model_fields}
    return LifeInsuranceData(**known)


def parse_life_insurance(text: str) -> dict:
    result = {
        "doc_type": "life_insurance",
        "premium_paid": 0.0,
        "parse_confidence": 0.0,
        "warnings": [],
    }
    if not text or len(text.strip()) < 20:
        result["warnings"].append("Document appears empty — could not extract premium amount.")
        return result

    try:
        data = extract_life_insurance_llm(text)
    except Exception as e:
        result["warnings"].append(
            f"Could not extract a life insurance premium figure from this document ({e}). "
            "Enter the amount manually."
        )
        return result

    if data.premium_paid <= 0:
        result["warnings"].append(
            "Could not find a premium amount on this document. Enter it manually."
        )
        return result

    result["premium_paid"] = data.premium_paid
    result["insurer_name"] = data.insurer_name
    result["parse_confidence"] = 0.75
    return result
