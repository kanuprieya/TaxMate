"""
Shared LLM-extraction helpers
================================
form16.py and capital_gains.py each grew a nearly identical "strip markdown
fences, then json.loads, falling back to slicing out the outermost {}/[]"
function. The newer single-object extractors (health_insurance.py,
life_insurance.py, home_loan.py, other_sources_income.py,
residential_status.py) all need the same object-parsing shape, so it moves
here rather than being copy-pasted a fifth and sixth time. form16.py/
capital_gains.py are left exactly as they are — not worth the churn of
switching two already-working call sites to import this instead.
"""

import json
import re


def parse_llm_json_object(raw: str) -> dict:
    """Strips markdown code fences if present, then parses a JSON object.
    Falls back to extracting the outermost {...} block if the model added
    stray text around it."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise
