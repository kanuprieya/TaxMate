"""
ITR-2 LangGraph Agent Pipeline
================================
Separate pipeline from graph/itr_graph.py (ITR-1) rather than a branch inside
it, per the project's ITR-1/ITR-2 separation plan — the two forms diverge
enough (multiple house properties, capital gains, foreign income) that
sharing one graph would mean conditionals threaded through every node.
graph/router.py is the only place that decides which of the two to invoke.

State machine:
  fill_form → compare_regimes → validate → score_confidence → explain → done

Mirrors itr_graph.py's node shape and AgentState conventions so both graphs
are easy to read side by side, but every node here is new code — itr_graph.py
itself is not imported or modified.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import TypedDict, Optional, Any
from datetime import datetime

from langgraph.graph import StateGraph, END

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from shared.itr2_schema import ITR2Form
from shared.tax_utils_itr2 import compute_tax_from_engine_itr2
from shared.tax_utils import float_safe
from shared.validator_itr2 import ITR2Validator
from shared.validator import TaxConfig


# ── Shared state schema ───────────────────────────────────────────────────────

class ITR2AgentState(TypedDict):
    session_id:          str
    ay:                  str
    raw_documents:       list[dict]    # outputs from doc-parser: form16 + capital_gains + property + foreign_income
    itr2_form:           dict          # serialised ITR2Form
    regime_analysis:     dict
    validation_flags:    list[dict]
    confidence_scores:   dict[str, dict]
    explanations:        dict[str, str]
    audit_trail:         list[dict]
    error:               Optional[str]
    step:                str
    integrity_score:     float


# ── Helper: merge documents of a given type into one extracted dict ──────────

def _merge_docs(docs: list[dict], doc_type: str, list_field: str) -> list:
    """ITR-2 filers can upload more than one capital-gains statement, more
    than one property, more than one foreign-income source — each becomes its
    own parsed document, so this merges all of a given doc_type's list_field
    into one flat list for the engine."""
    merged: list = []
    for d in docs:
        if d.get("doc_type") == doc_type:
            merged.extend(d.get("data", {}).get(list_field, []))
    return merged


# ── Node 1: Merge documents into ITR-2 form ────────────────────────────────────

def node_fill_form(state: ITR2AgentState) -> dict:
    """Maps parsed documents (Form 16 + capital gains + property + foreign
    income) onto ITR-2 fields via the same config-driven engine ITR-1 uses,
    routed through the ITR-2 configs/primitives instead."""
    docs = state["raw_documents"]
    form = ITR2Form()
    audit = list(state.get("audit_trail", []))
    validation_flags = list(state.get("validation_flags", []))

    form16_doc = next((d for d in docs if d.get("doc_type") == "form16"), None)
    if not form16_doc:
        return {**state, "error": "No Form 16 found. Please upload Form 16.", "step": "fill_form"}

    extracted = dict(form16_doc.get("data", {}))
    if not extracted:
        return {**state, "error": "Invalid Form 16 data structure.", "step": "fill_form"}

    extracted["house_properties"] = _merge_docs(docs, "property", "house_properties")
    extracted["capital_gains_raw"] = _merge_docs(docs, "capital_gains", "capital_gains_raw")
    extracted["foreign_income"] = _merge_docs(docs, "foreign_income", "foreign_income")
    foreign_assets = _merge_docs(docs, "foreign_income", "foreign_assets")

    ay_str = extracted.get("assessment_year") or state.get("ay", "AY2026-27")
    if not ay_str.startswith("AY"): ay_str = f"AY{ay_str}"

    try:
        analysis = compute_tax_from_engine_itr2(extracted, ay=ay_str)
        comp = analysis["computed"]

        form.personal_info.assessment_year = ay_str
        form.personal_info.pan = extracted.get("employee_pan")
        form.personal_info.first_name = extracted.get("employee_name")

        if analysis.get("status") == "needs_review":
            v_result = type("V", (), {
                "status": "needs_review", "integrity_score": 0.0, "errors": [], "warnings": [],
            })()
        else:
            validator = ITR2Validator(TaxConfig(assessment_year=_ay_year(ay_str)))
            v_result = validator.validate(extracted, comp)

        if v_result.status == "needs_review":
            for err in v_result.errors:
                validation_flags.append({
                    "field": "engine", "severity": "error", "message": err,
                    "suggestion": "Review your uploaded documents or manually enter missing values.",
                })

        def val(k):
            v = comp.get(k)
            if v == "missing" or v is None: return 0.0
            return float_safe(v)

        form.salary_income.gross_salary = val("gross_salary")
        form.salary_income.net_salary = val("salary_income")
        form.salary_income.taxable_salary = val("taxable_salary")
        form.salary_income.standard_deduction_16ia = val("standard_deduction")
        form.salary_income.professional_tax_16iii = val("professional_tax")

        form.house_property_loss_carried_forward = val("house_property_loss_carried_forward")

        cg = comp.get("capital_gains", {}) or {}
        form.capital_gains_summary.stcg_111a = float_safe(cg.get("stcg_111a", 0.0))
        form.capital_gains_summary.ltcg_112a = float_safe(cg.get("ltcg_112a", 0.0))
        form.capital_gains_summary.ltcg_112_other = float_safe(cg.get("ltcg_112_other", 0.0))
        form.capital_gains_summary.capital_gains_tax = val("capital_gains_tax")

        form.foreign_tax_credit_relief = val("foreign_tax_credit_relief")

        if analysis["regime_used"] == "old":
            form.deductions.sec_80c = extracted.get("chapter_6A", {}).get("80C", 0.0)
            form.deductions.sec_80d = extracted.get("chapter_6A", {}).get("80D", 0.0)
            form.deductions.total_deductions = extracted.get("chapter_6A", {}).get("total", 0.0)

        form.tax_computation.regime = analysis["regime_used"]
        form.tax_computation.gross_total_income = val("gross_total_income")
        form.tax_computation.taxable_income = val("taxable_income")
        form.tax_computation.tax_before_rebate = val("tax_before_rebate")
        form.tax_computation.rebate_87a = val("rebate_87a")
        form.tax_computation.surcharge = val("surcharge")
        form.tax_computation.health_education_cess = val("cess")
        form.tax_computation.total_tax_liability = val("total_tax_liability")
        form.tax_computation.tds_deducted = val("tds_deducted")

        rop = val("refund_or_payable")
        if rop > 0:
            form.tax_computation.refund = rop
            form.tax_computation.tax_payable = 0.0
        else:
            form.tax_computation.refund = 0.0
            form.tax_computation.tax_payable = abs(rop)

        form.other_sources.savings_bank_interest = extracted.get("other_income", {}).get("other_sources", 0.0)

        audit.append({
            "timestamp": datetime.utcnow().isoformat(),
            "node": "fill_form",
            "action": "ITR-2 tax engine computation completed",
            "status": v_result.status,
            "regime": analysis["regime_used"],
        })

        return {
            **state,
            "itr2_form": form.model_dump(),
            "regime_analysis": analysis,
            "status": analysis["status"] if v_result.status == "ok" else "needs_review",
            "validation_flags": validation_flags + analysis.get("errors", []) + analysis.get("warnings", []),
            "audit_trail": audit,
            "integrity_score": v_result.integrity_score,
            "step": "fill_form",
        }

    except Exception as e:
        import traceback
        print(f"ITR2_NODE_FILL_FORM CRASH: {str(e)}\n{traceback.format_exc()}")
        return {**state, "status": "needs_review", "error": f"Pipeline logic error: {str(e)}", "step": "fill_form"}


def _ay_year(ay_str: str) -> int:
    try:
        return int(ay_str.replace("AY", "").split("-")[0])
    except Exception:
        return 2026


# ── Node 2: Compare regimes ───────────────────────────────────────────────────

def node_compare_regimes(state: ITR2AgentState) -> dict:
    """Same strict-locking rule as ITR-1: never compute both regimes unless
    explicitly requested; here purely for the side-by-side comparison display."""
    analysis = state.get("regime_analysis", {})
    if not analysis or "computed" not in analysis or analysis.get("status") == "needs_review":
        return {**state, "step": "validate"}

    is_locked = analysis.get("regime_used") != "missing"
    extracted = analysis.get("extracted", {})
    ay = state.get("ay", "AY2026-27")
    current_regime = analysis.get("regime_used", "new")
    other_regime = "new" if current_regime == "old" else "old"

    import copy
    extracted_other = copy.deepcopy(extracted)
    extracted_other["tax_regime"] = other_regime
    analysis_other = compute_tax_from_engine_itr2(extracted_other, ay=ay)

    curr_tax = float_safe(analysis["computed"].get("total_tax_liability"))
    other_tax = float_safe(analysis_other["computed"].get("total_tax_liability"))

    comparison = {
        "status": analysis["status"],
        "current_regime": current_regime,
        "best_regime": current_regime if curr_tax <= other_tax else other_regime,
        "savings": abs(curr_tax - other_tax),
        "old_regime_tax": curr_tax if current_regime == "old" else other_tax,
        "new_regime_tax": curr_tax if current_regime == "new" else other_tax,
        "computed": analysis["computed"],
        "is_locked": is_locked,
        "extracted": extracted,
    }

    audit = list(state.get("audit_trail", []))
    audit.append({
        "timestamp": datetime.utcnow().isoformat(),
        "node": "compare_regimes",
        "action": "ITR-2 Strict Regime Comparison Completed",
        "best": comparison["best_regime"],
        "savings": comparison["savings"],
    })

    return {**state, "regime_analysis": comparison, "audit_trail": audit, "step": "validate"}


# ── Node 3: Validate ──────────────────────────────────────────────────────────

def node_validate(state: ITR2AgentState) -> dict:
    form_data = state["itr2_form"]
    flags = list(state.get("validation_flags", []))

    analysis = state.get("regime_analysis", {})
    extracted = analysis.get("extracted", {})
    computed = analysis.get("computed", {})

    if analysis.get("status") == "needs_review":
        return {**state, "validation_flags": flags, "integrity_score": 0.0, "step": "score_confidence"}

    validator = ITR2Validator(TaxConfig(assessment_year=_ay_year(state.get("ay", "AY2026-27"))))
    v_result = validator.validate(extracted, computed)

    for err in v_result.errors:
        flags.append({
            "field": "integrity", "severity": "error", "message": err,
            "suggestion": "Correct this field manually to ensure accurate filing.",
        })
    for warn in v_result.warnings:
        flags.append({
            "field": "integrity", "severity": "warning", "message": warn,
            "suggestion": "Verify this value against your source documents.",
        })

    return {**state, "validation_flags": flags, "integrity_score": v_result.integrity_score, "step": "score_confidence"}


# ── Node 4: Confidence scoring ───────────────────────────────────────────────

def node_score_confidence(state: ITR2AgentState) -> dict:
    flags = state.get("validation_flags", [])
    errors = [f for f in flags if f["severity"] == "error"]
    warnings = [f for f in flags if f["severity"] == "warning"]

    base_score = 0.95
    penalty = (len(errors) * 0.3) + (len(warnings) * 0.05)
    confidence = max(0.1, base_score - penalty)

    return {
        **state,
        "confidence_scores": {**state.get("confidence_scores", {}), "overall": confidence},
        "step": "explain",
    }


# ── Node 5: Explanation ────────────────────────────────────────────────────────

def node_explain(state: ITR2AgentState) -> dict:
    form = state["itr2_form"]
    analysis = state.get("regime_analysis", {})

    if analysis.get("status") == "needs_review":
        summary = ("Tax computation could not be completed. One or more mandatory fields "
                   "(Gross Salary, TDS, or Regime) are missing or inconsistent. "
                   "Please review the 'Needs Review' flags.")
        return {**state, "explanations": {"summary": summary}, "step": "done"}

    tax_comp = form.get("tax_computation", {})
    cg = form.get("capital_gains_summary", {})
    regime = analysis.get("current_regime", analysis.get("regime_used", "selected")).upper()

    lines = [f"**ITR-2 Computation Summary ({regime} REGIME)**", ""]
    lines.append(f"1. Gross Total Income: Rs {tax_comp.get('gross_total_income', 0):,.0f}")

    hp_loss_cf = form.get("house_property_loss_carried_forward", 0.0)
    if hp_loss_cf > 0:
        lines.append(f"   House property loss carried forward: Rs {hp_loss_cf:,.0f}")

    total_cg = cg.get("stcg_111a", 0) + cg.get("ltcg_112a", 0) + cg.get("ltcg_112_other", 0)
    if total_cg > 0:
        lines.append("")
        lines.append(f"2. Capital Gains (taxed at special rates, not slab rates):")
        if cg.get("stcg_111a", 0) > 0:
            lines.append(f"   STCG u/s 111A: Rs {cg['stcg_111a']:,.0f}")
        if cg.get("ltcg_112a", 0) > 0:
            lines.append(f"   LTCG u/s 112A: Rs {cg['ltcg_112a']:,.0f}")
        if cg.get("ltcg_112_other", 0) > 0:
            lines.append(f"   LTCG u/s 112 (other assets): Rs {cg['ltcg_112_other']:,.0f}")
        lines.append(f"   Capital gains tax: Rs {cg.get('capital_gains_tax', 0):,.0f}")

    lines.append("")
    taxable_income = tax_comp.get("taxable_income", 0.0)
    total_tax = tax_comp.get("total_tax_liability", 0.0)
    lines.append(f"3. Taxable Income (slab-rate portion): Rs {taxable_income:,.0f}")
    lines.append(f"   = **Total Tax Liability: Rs {total_tax:,.0f}**")

    ftc = form.get("foreign_tax_credit_relief", 0.0)
    if ftc > 0:
        lines.append(f"   (-) Foreign Tax Credit (Sec 90/91): Rs {ftc:,.0f}")

    lines.append("")
    refund = tax_comp.get("refund", 0.0)
    payable = tax_comp.get("tax_payable", 0.0)
    lines.append(f"4. TDS Deducted: Rs {tax_comp.get('tds_deducted', 0):,.0f}")
    if refund > 0:
        lines.append(f"   **Refund Due: Rs {refund:,.0f}**")
    elif payable > 0:
        lines.append(f"   **Tax Payable: Rs {payable:,.0f}**")
    else:
        lines.append("   No tax refund or payable.")

    summary = "\n".join(lines)

    explanations = {"summary": summary}
    if analysis.get("best_regime") and analysis.get("savings", 0) > 0:
        best = analysis["best_regime"].upper()
        explanations["regime_recommendation"] = (
            f"**{best} tax regime recommended.** "
            f"Old regime tax: Rs {analysis.get('old_regime_tax', 0):,.0f} | "
            f"New regime tax: Rs {analysis.get('new_regime_tax', 0):,.0f} | "
            f"Saving: Rs {analysis['savings']:,.0f}"
        )

    return {**state, "explanations": explanations, "step": "done"}


# ── Graph Construction ────────────────────────────────────────────────────────

def create_itr2_graph():
    workflow = StateGraph(ITR2AgentState)

    workflow.add_node("fill_form", node_fill_form)
    workflow.add_node("compare_regimes", node_compare_regimes)
    workflow.add_node("validate", node_validate)
    workflow.add_node("score_confidence", node_score_confidence)
    workflow.add_node("explain", node_explain)

    workflow.set_entry_point("fill_form")
    workflow.add_edge("fill_form", "compare_regimes")
    workflow.add_edge("compare_regimes", "validate")
    workflow.add_edge("validate", "score_confidence")
    workflow.add_edge("score_confidence", "explain")
    workflow.add_edge("explain", END)

    return workflow.compile()


def run_itr2_pipeline(parsed_documents: list[dict], session_id: str, ay: str = "AY2026-27"):
    graph = create_itr2_graph()
    initial_state = {
        "session_id": session_id,
        "ay": ay,
        "raw_documents": parsed_documents,
        "itr2_form": ITR2Form().model_dump(),
        "regime_analysis": {},
        "validation_flags": [],
        "confidence_scores": {},
        "explanations": {},
        "audit_trail": [],
        "error": None,
        "step": "start",
        "integrity_score": 1.0,
    }
    return graph.invoke(initial_state)
