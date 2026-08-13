"""
Other Sources Income Summary Parser
=======================================
ITR-2 Schedule OS — domestic dividends and bank/FD interest summaries
(e.g. a broker's annual dividend statement, a bank's quarterly savings-
interest summary). These are NOT bank passbook statements — they list
income by source/period, not a debit/credit transaction ledger — so
bank_statement.py's row-classifier (built for transaction rows) doesn't
fit them; this is a distinct doc type with its own LLM extraction.

Deliberately domestic-only: a document showing foreign-currency amounts is
not this doc type's job — doc-parser/main.py's foreign-currency-signal
scan runs on this doc type's raw text too and flags it out of scope before
it would ever reach here as a false "other sources income" figure.
"""

import sys
from pathlib import Path
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers._llm_common import parse_llm_json_object


class OtherSourcesData(BaseModel):
    dividends: float = 0.0
    savings_interest: float = 0.0
    fd_interest: float = 0.0
    other_interest: float = 0.0


OTHER_SOURCES_SYSTEM_PROMPT = """You are extracting data from an Indian domestic income summary (dividend statement, savings account interest summary, or fixed deposit interest summary) for ITR-2 Schedule OS. The document may list many individual line items (per company, per bank, per quarter) that need to be summed.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with exactly these keys:

{
  "dividends": number,          // sum of all dividend income shown (from shares/mutual funds), 0 if none
  "savings_interest": number,   // sum of all savings bank account interest shown, 0 if none
  "fd_interest": number,        // sum of all fixed/recurring deposit interest shown, 0 if none
  "other_interest": number      // any other interest income that doesn't fit the above (e.g. bond interest), 0 if none
}

Rules:
- All numbers must be plain numbers: no currency symbols, no commas.
- Sum every line item that belongs to the same bucket — do not return just the first row.
- If the document is entirely in a foreign currency (not INR), still extract the numbers as shown — a separate check elsewhere decides whether foreign income is in scope, not you.
- Return ONLY the JSON object. No explanation, no markdown code fences."""


def extract_other_sources_llm(text: str) -> OtherSourcesData:
    from shared.llm_client import complete_with_system

    raw_response = complete_with_system(
        system=OTHER_SOURCES_SYSTEM_PROMPT,
        user=f"Other sources income document raw extracted text:\n\n{text}",
        temperature=0.0,
    )
    parsed = parse_llm_json_object(raw_response)
    known = {k: v for k, v in parsed.items() if k in OtherSourcesData.model_fields}
    return OtherSourcesData(**known)


def parse_other_sources_income(text: str) -> dict:
    result = {
        "doc_type": "other_sources_income",
        "dividends": 0.0,
        "savings_interest": 0.0,
        "fd_interest": 0.0,
        "other_interest": 0.0,
        "parse_confidence": 0.0,
        "warnings": [],
    }
    if not text or len(text.strip()) < 20:
        result["warnings"].append("Document appears empty — could not extract income figures.")
        return result

    try:
        data = extract_other_sources_llm(text)
    except Exception as e:
        result["warnings"].append(
            f"Could not extract income figures from this document ({e}). Enter them manually."
        )
        return result

    total = data.dividends + data.savings_interest + data.fd_interest + data.other_interest
    if total <= 0:
        result["warnings"].append(
            "Could not find any dividend/interest figures on this document. Enter them manually."
        )
        return result

    result["dividends"] = data.dividends
    result["savings_interest"] = data.savings_interest
    result["fd_interest"] = data.fd_interest
    result["other_interest"] = data.other_interest
    result["parse_confidence"] = 0.75
    return result
