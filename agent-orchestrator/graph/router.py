"""
ITR-1 / ITR-2 Eligibility Router
====================================
The ONLY place in this codebase that decides (a) whether a filing is out of
scope entirely (currently: foreign income/assets — Schedule FA/FSI) and, if
not, (b) which of the two form pipelines (graph.itr_graph vs graph.itr2_graph)
it goes through. Neither graph imports or branches on the other, and neither
has any foreign-income handling — main.py calls exactly this module to find
out what to do.

Eligibility here approximates the real ITR-1 (Sahaj) eligibility rules for
AY 2026-27 specifically, based purely on which document types were uploaded
(no full profile questionnaire). Verified against CBDT Notification No.
45/2026 and cross-checked across sources (Jul 2026) rather than assumed from
general/prior-year knowledge — AY 2026-27 relaxed two limits that earlier
years didn't have, which an earlier version of this router got wrong before
this check:
  - Up to TWO house properties are ITR-1-eligible (was one in prior years).
    Only three or more disqualifies.
  - LTCG under Sec 112A up to Rs 1,25,000 (i.e. within the exemption
    threshold) is itself ITR-1-eligible. Only capital gains that exceed
    that — any Sec 111A STCG, any non-equity gain, or 112A LTCG above the
    threshold — require ITR-2. A filer with a small 112A gain and nothing
    else should not be bounced to ITR-2 unnecessarily.
  - Any foreign income/assets -> out of scope entirely, not just "needs
    ITR-2" (see is_out_of_scope below — this is a deliberate scope decision,
    not something either graph attempts and gets wrong).
  - Total income > Rs 50 lakh -> not eligible for ITR-1 (estimated here from
    raw extracted figures since the authoritative computation only happens
    inside whichever graph gets chosen — see _estimate_total_income).
This is still a document-driven approximation, not the full CBDT eligibility
matrix (which also covers agricultural income > Rs 5,000, business/
professional income, directorship in a company, unlisted equity holdings,
TDS u/s 194N, and deferred ESOP tax) — a fuller version would need those
signals surfaced from extraction or asked of the user directly.
"""

from __future__ import annotations
from typing import Optional

from shared.tax_utils import float_safe
from shared.tax_engine.primitives import compute_capital_gains

FOREIGN_INCOME_DOC_TYPE = "foreign_income"
MAX_ITR1_HOUSE_PROPERTIES = 2
MAX_ITR1_TOTAL_INCOME = 5000000
LTCG_112A_EXEMPTION = 125000


def is_out_of_scope(parsed_documents: list[dict]) -> Optional[str]:
    """Returns a user-facing redirect message if this filing has income this
    codebase deliberately doesn't compute (currently: any foreign income or
    asset disclosure — Schedule FA/FSI), or None if it's in scope for one of
    the two graphs. Checked before determine_form_type so an out-of-scope
    filing never enters either pipeline half-computed — full DTAA/Schedule FA
    computation was cut after scoping it out as more than this pass has time
    to get right, so this stays a flag-and-redirect, not an attempt."""
    foreign_docs = [d for d in parsed_documents if d.get("doc_type") == FOREIGN_INCOME_DOC_TYPE]
    if not foreign_docs:
        return None

    countries = sorted({
        entry.get("country")
        for d in foreign_docs
        for entry in (d.get("data", {}).get("foreign_income", []) + d.get("data", {}).get("foreign_assets", []))
        if entry.get("country")
    })
    where = f" ({', '.join(countries)})" if countries else ""

    return (
        f"Foreign income and/or foreign asset disclosure (Schedule FSI/FA){where} "
        "was detected in your documents. This is out of scope for automated "
        "computation in this tool — please consult a tax professional for "
        "this filing rather than relying on this result."
    )


def _merged_capital_gains_buckets(parsed_documents: list[dict]) -> dict:
    """Runs the same classification the real engine uses (rather than
    re-deriving STCG/LTCG bucketing rules here) so the eligibility check
    reflects actual bucket totals, not just "were any capital gains
    documents uploaded at all"."""
    raw_txns = []
    for d in parsed_documents:
        if d.get("doc_type") == "capital_gains":
            raw_txns.extend(d.get("data", {}).get("capital_gains_raw", []))
    if not raw_txns:
        return {"stcg_111a": 0.0, "ltcg_112a": 0.0, "ltcg_112_other": 0.0, "stcg_slab": 0.0}
    state = compute_capital_gains({"capital_gains_raw": raw_txns}, {})
    buckets = dict(state.get("capital_gains", {}))
    buckets["stcg_slab"] = state.get("other_source_income", 0.0)  # folded in by compute_capital_gains
    return buckets


def _estimate_total_income(parsed_documents: list[dict], capital_gains_buckets: dict) -> float:
    """A rough pre-computation estimate for the Rs 50L threshold check only
    — NOT the authoritative total income figure. The chosen graph (ITR-1 or
    ITR-2) computes that precisely, with proper deductions/exemptions/loss
    set-offs applied; this just needs to be in the right ballpark to decide
    which graph should run."""
    total = 0.0
    for d in parsed_documents:
        if d.get("doc_type") == "form16":
            data = d.get("data", {})
            total += float_safe(data.get("gross_salary", {}).get("total", 0.0))
            other_income = data.get("other_income", {})
            total += float_safe(other_income.get("house_property", 0.0))
            total += float_safe(other_income.get("other_sources", 0.0))
        elif d.get("doc_type") == "property":
            for prop in d.get("data", {}).get("house_properties", []):
                total += float_safe(prop.get("annual_value", 0.0))
    total += sum(float_safe(v) for v in capital_gains_buckets.values())
    return total


def determine_form_type(parsed_documents: list[dict]) -> str:
    """Returns 'itr1' or 'itr2' based on the uploaded document set.
    Precondition: is_out_of_scope(parsed_documents) is None — this function
    doesn't itself check for foreign income, since run_pipeline() below
    always checks that first and short-circuits before this is ever called."""
    total_properties = sum(
        len(d.get("data", {}).get("house_properties", []))
        for d in parsed_documents
        if d.get("doc_type") == "property"
    )
    if total_properties > MAX_ITR1_HOUSE_PROPERTIES:
        return "itr2"

    cg = _merged_capital_gains_buckets(parsed_documents)
    if cg["stcg_111a"] > 0 or cg["ltcg_112_other"] > 0 or cg["stcg_slab"] > 0:
        return "itr2"
    if cg["ltcg_112a"] > LTCG_112A_EXEMPTION:
        return "itr2"

    if _estimate_total_income(parsed_documents, cg) > MAX_ITR1_TOTAL_INCOME:
        return "itr2"

    return "itr1"


def run_pipeline(parsed_documents: list[dict], session_id: str, ay: str = "AY2026-27") -> tuple[str, dict]:
    """Dispatches to the appropriate graph, or short-circuits with a
    flag-and-redirect result if the filing is out of scope entirely (see
    is_out_of_scope). Returns (form_type, result); form_type is 'unsupported'
    for the out-of-scope case, which main.py's existing `result.get("error")`
    handling already surfaces correctly with no further branching needed —
    'unsupported' just makes it explicit that no computation was attempted,
    as opposed to 'itr1'/'itr2' having attempted one and hit a data problem."""
    redirect_message = is_out_of_scope(parsed_documents)
    if redirect_message:
        return "unsupported", {
            "session_id": session_id,
            "ay": ay,
            "error": redirect_message,
            "step": "blocked",
        }

    form_type = determine_form_type(parsed_documents)

    if form_type == "itr2":
        from graph.itr2_graph import run_itr2_pipeline
        result = run_itr2_pipeline(parsed_documents, session_id, ay)
    else:
        from graph.itr_graph import run_itr_pipeline
        result = run_itr_pipeline(parsed_documents, session_id, ay)

    return form_type, result
