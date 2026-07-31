"""
ITR-1 / ITR-2 Eligibility Router
====================================
The ONLY place in this codebase that decides which of the two form
pipelines (graph.itr_graph vs graph.itr2_graph) a filing goes through.
Neither graph imports or branches on the other — main.py calls exactly this
module to find out which one to run.

Eligibility here approximates the real ITR-1 (Sahaj) eligibility rules,
based purely on which document types were uploaded:
  - Any capital gains (of any kind) -> not eligible for ITR-1.
  - Any foreign income/assets -> not eligible for ITR-1.
  - More than one house property -> not eligible for ITR-1.
This is a document-driven approximation, not the full CBDT eligibility
matrix (which also covers agricultural income > Rs 5,000, business/
professional income, directorship in a company, unlisted equity holdings,
and total income > Rs 50 lakh) — a fuller version would need those signals
surfaced from extraction or asked of the user directly.
"""

from __future__ import annotations

ITR2_TRIGGER_DOC_TYPES = {"capital_gains", "foreign_income"}


def determine_form_type(parsed_documents: list[dict]) -> str:
    """Returns 'itr1' or 'itr2' based on the uploaded document set."""
    if any(d.get("doc_type") in ITR2_TRIGGER_DOC_TYPES for d in parsed_documents):
        return "itr2"

    total_properties = sum(
        len(d.get("data", {}).get("house_properties", []))
        for d in parsed_documents
        if d.get("doc_type") == "property"
    )
    if total_properties > 1:
        return "itr2"

    return "itr1"


def run_pipeline(parsed_documents: list[dict], session_id: str, ay: str = "AY2026-27") -> tuple[str, dict]:
    """Dispatches to the appropriate graph. Returns (form_type, result) so
    the caller (agent-orchestrator/main.py) knows which response shape and
    which node functions (for update-field/export) apply to this session."""
    form_type = determine_form_type(parsed_documents)

    if form_type == "itr2":
        from graph.itr2_graph import run_itr2_pipeline
        result = run_itr2_pipeline(parsed_documents, session_id, ay)
    else:
        from graph.itr_graph import run_itr_pipeline
        result = run_itr_pipeline(parsed_documents, session_id, ay)

    return form_type, result
