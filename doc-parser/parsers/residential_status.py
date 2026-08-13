"""
Residential Status Worksheet Parser
=======================================
ITR-2 — determines Resident (ROR) / Resident-but-Not-Ordinarily-Resident
(RNOR) / Non-Resident (NR) status under Sec 6. Only Resident-and-Ordinarily-
Resident filings are computed by this app — RNOR/NR filers are taxed on a
different (source-based, DTAA-affected) basis this codebase doesn't model,
same reasoning as the existing foreign-income scope decision. See
graph/router.py's is_out_of_scope(), which reads this doc type's `status`
field to redirect those filings rather than computing them wrong.
"""

import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers._llm_common import parse_llm_json_object


class ResidentialStatusData(BaseModel):
    status: Optional[str] = None  # "resident" | "rnor" | "non_resident"
    days_in_india_current_year: Optional[float] = None


RESIDENTIAL_STATUS_SYSTEM_PROMPT = """You are extracting the determined residential status from an Indian tax residential-status worksheet (Sec 6 of the Income-tax Act) for an ITR-2 filing for Assessment Year 2026-27 (Financial Year 2025-26) — this is the ONLY assessment year this tool supports, so you are always looking for the status that applies to AY 2026-27 / FY 2025-26 specifically, never an earlier year's status.

Worksheets come in two common shapes:
1. A single year's day-count calculation ending in one stated conclusion — use that conclusion.
2. A multi-year LOOKBACK TABLE, one row per Assessment Year (a common real format: a CA firm tracking a filer's status history year over year, e.g. because someone moved abroad and has been Non-Resident for several years before recently returning to India). In this shape, do NOT return the oldest row, the most common status across rows, or any row from a year other than 2026-27 — find the row whose Assessment Year column reads "2026-27" (or Financial Year column reads "2025-26") and return THAT row's status, even if every other row in the table says something different.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "status": "resident" or "rnor" or "non_resident" or null,  // the status for AY 2026-27 / FY 2025-26 specifically. "resident" means Resident and Ordinarily Resident (ROR) — a worksheet that only says "Resident" without discussing ordinarily-resident conditions should still map to "resident". Use null only if no AY 2026-27/FY 2025-26 row or conclusion is stated anywhere.
  "days_in_india_current_year": number or null   // days physically present in India in FY 2025-26, if stated
}

Rules:
- Read the whole document. If it's a day-count worksheet, use its final conclusion, not an intermediate calculation. If it's a multi-year table, match the row by year as described above.
- Return ONLY the JSON object. No explanation, no markdown code fences."""


def extract_residential_status_llm(text: str) -> ResidentialStatusData:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=RESIDENTIAL_STATUS_SYSTEM_PROMPT,
        user=f"Residential status worksheet raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = parse_llm_json_object(raw_response)
    known = {k: v for k, v in parsed.items() if k in ResidentialStatusData.model_fields}
    return ResidentialStatusData(**known)


def parse_residential_status(text: str) -> dict:
    result = {
        "doc_type": "residential_status",
        "status": None,
        "days_in_india_current_year": None,
        "parse_confidence": 0.0,
        "warnings": [],
    }
    if not text or len(text.strip()) < 20:
        result["warnings"].append("Document appears empty — could not determine residential status.")
        return result

    try:
        data = extract_residential_status_llm(text)
    except Exception as e:
        result["warnings"].append(
            f"Could not determine residential status from this document ({e}). "
            "Confirm it manually — this affects whether this app can compute your return at all."
        )
        return result

    if data.status not in ("resident", "rnor", "non_resident"):
        result["warnings"].append(
            "Could not find a clear residential status determination on this document. "
            "Confirm it manually — this affects whether this app can compute your return at all."
        )
        return result

    result["status"] = data.status
    result["days_in_india_current_year"] = data.days_in_india_current_year
    result["parse_confidence"] = 0.75
    return result
