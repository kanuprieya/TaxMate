"""
ITR-2 LangGraph Agent Pipeline
================================
Separate pipeline from graph/itr_graph.py (ITR-1) rather than a branch inside
it, per the project's ITR-1/ITR-2 separation plan — the two forms diverge
enough (multiple house properties, capital gains, foreign income) that
sharing one graph would mean conditionals threaded through every node.
graph/router.py is the only place that decides which of the two to invoke;
it still flags and redirects Non-Resident/RNOR filings before either graph
runs, but a Resident's foreign income is real input this pipeline computes
(Schedule FSI) rather than something it's shielded from.

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
from shared.itr2_schema import ITR2Form, ForeignIncomeEntry, ForeignAssetEntry
from shared.tax_utils_itr2 import compute_tax_from_engine_itr2
from shared.tax_utils import float_safe
from shared.validator_itr2 import ITR2Validator
from shared.validator import TaxConfig


# ── Shared state schema ───────────────────────────────────────────────────────

class ITR2AgentState(TypedDict):
    session_id:          str
    ay:                  str
    raw_documents:       list[dict]    # outputs from doc-parser: form16 + capital_gains + property
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
    than one property — each becomes its own parsed document, so this merges
    all of a given doc_type's list_field into one flat list for the engine."""
    merged: list = []
    for d in docs:
        if d.get("doc_type") == doc_type:
            merged.extend(d.get("data", {}).get(list_field, []))
    return merged


def _sum_docs(docs: list[dict], doc_type: str, field: str) -> float:
    """Same idea as _merge_docs but for the 'Other Inputs, Deductions &
    Disclosures' doc types (health_insurance, life_insurance, home_loan,
    other_sources_income) — a filer can upload more than one premium
    receipt/interest certificate, and each parser's amount field is a single
    number rather than a list, so these sum instead of extend."""
    return sum(float_safe(d.get("data", {}).get(field, 0.0)) for d in docs if d.get("doc_type") == doc_type)


# ── Node 1: Merge documents into ITR-2 form ────────────────────────────────────

def node_fill_form(state: ITR2AgentState) -> dict:
    """Maps parsed documents (Form 16, capital gains, property, foreign
    income, deduction/other-income docs) onto ITR-2 fields via the same
    config-driven engine ITR-1 uses, routed through the ITR-2 configs/
    primitives instead. Form 16 isn't mandatory — see the form16_doc branch
    below for filers whose income is entirely foreign/property/capital-gains."""
    docs = state["raw_documents"]
    form = ITR2Form()
    audit = list(state.get("audit_trail", []))
    validation_flags = list(state.get("validation_flags", []))

    form16_doc = next((d for d in docs if d.get("doc_type") == "form16"), None)
    has_other_income_doc = any(
        d.get("doc_type") in ("foreign_income", "property", "capital_gains", "other_sources_income") and d.get("data")
        for d in docs
    )
    if not form16_doc and not has_other_income_doc:
        return {**state, "error": "No income documents found. Please upload Form 16 or another income document.", "step": "fill_form"}

    if form16_doc:
        extracted = dict(form16_doc.get("data", {}))
        if not extracted:
            return {**state, "error": "Invalid Form 16 data structure.", "step": "fill_form"}
    else:
        # A filer can have real ITR-2 income (entirely foreign salary,
        # or only property/capital-gains income) with no domestic Form 16
        # at all — found against a real "Salary Income...AED" workbook
        # uploaded on its own, which previously hard-failed the whole
        # pipeline with "No Form 16 found" even though there was a real
        # return to compute. employee_name/tax_regime/tds have no other
        # source right now (no document type here extracts a filer's own
        # name), so a placeholder is used and flagged rather than blocking
        # computation entirely — editable afterward via the field-edit UI.
        extracted = {
            "employee_name": "Filer (name not detected — edit this field)",
            "tax_regime": "new",
            "tds": 0,
            "gross_salary": {"total": 0},
            "other_income": {"house_property": 0, "other_sources": 0},
            "chapter_6A": {"80C": 0, "80D": 0},
        }
        validation_flags.append({
            "field": "personal_info.first_name", "severity": "warning",
            "message": "No Form 16 was uploaded, so your name and tax regime could not be detected.",
            "suggestion": "Upload a Form 16 if you have domestic salary, or edit your name and regime manually.",
        })

    extracted["house_properties"] = _merge_docs(docs, "property", "house_properties")
    extracted["capital_gains_raw"] = _merge_docs(docs, "capital_gains", "capital_gains_raw")
    extracted["foreign_income_raw"] = _merge_docs(docs, "foreign_income", "foreign_income")
    foreign_assets_raw = _merge_docs(docs, "foreign_income", "foreign_assets")
    extracted["foreign_assets_raw"] = foreign_assets_raw

    # NR/RNOR filers never reach this graph — graph/router.py's
    # is_out_of_scope() redirects them before run_itr2_pipeline is ever
    # called — so any residential_status doc present here has already been
    # confirmed "resident". No doc at all means residency was simply never
    # checked, not that it's safe to assume: this whole computation is only
    # valid for a Resident and Ordinarily Resident filer, and foreign salary
    # for an NR/RNOR is often not taxable in India at all (Sec 5(1)/5(2)).
    residential_status_doc = next((d for d in docs if d.get("doc_type") == "residential_status"), None)
    residential_status_confirmed = bool(
        residential_status_doc and residential_status_doc.get("data", {}).get("status") == "resident"
    )
    extracted["residential_status_confirmed"] = residential_status_confirmed
    if extracted["foreign_income_raw"] and not residential_status_confirmed:
        validation_flags.append({
            "field": "personal_info.residential_status", "severity": "warning",
            "message": (
                "Residential status was not confirmed, but foreign income was detected. This "
                "entire computation assumes you are Resident and Ordinarily Resident (ROR) for "
                "this year. If you are actually Non-Resident or RNOR, foreign salary for "
                "services rendered outside India is often NOT taxable in India at all under "
                "Sec 5 — this result may not apply to you."
            ),
            "suggestion": "Upload a residential status worksheet, or confirm your Sec 6 status with a tax professional, before relying on this figure.",
        })
    if extracted["foreign_income_raw"] and not foreign_assets_raw:
        validation_flags.append({
            "field": "foreign_assets", "severity": "warning",
            "message": (
                "Foreign income was detected but no foreign asset disclosure was found. If this "
                "income was credited to a foreign bank account (or you hold any other foreign "
                "asset), that account must be separately disclosed under Schedule FA — this is "
                "independent of the income-tax computation above."
            ),
            "suggestion": "Non-disclosure carries a flat penalty of Rs 10 lakh per undisclosed foreign account per year under the Black Money Act — upload your foreign account statement if applicable.",
        })

    # "Other Inputs, Deductions & Disclosures" documents: dedicated
    # certificates (health/life insurance premium, home loan principal,
    # dividend/domestic-interest summaries) are a more authoritative source
    # than Form 16's own chapter_6A/other_income fields — which are often a
    # stale payroll-department declaration — so these ADD on top rather than
    # replace. home_loan's interest_paid is deliberately never added here:
    # that would double-count against property.py's per-property
    # interest_on_loan_24b for the same loan (see home_loan.py's docstring).
    extra_80d = _sum_docs(docs, "health_insurance", "premium_paid")
    extra_80c = _sum_docs(docs, "life_insurance", "premium_paid") + _sum_docs(docs, "home_loan", "principal_repaid")
    extra_other_sources = (
        _sum_docs(docs, "other_sources_income", "dividends")
        + _sum_docs(docs, "other_sources_income", "savings_interest")
        + _sum_docs(docs, "other_sources_income", "fd_interest")
        + _sum_docs(docs, "other_sources_income", "other_interest")
    )
    if extra_80d or extra_80c:
        chapter_6a = dict(extracted.get("chapter_6A", {}))
        chapter_6a["80D"] = float_safe(chapter_6a.get("80D", 0.0)) + extra_80d
        chapter_6a["80C"] = float_safe(chapter_6a.get("80C", 0.0)) + extra_80c
        chapter_6a["total"] = float_safe(chapter_6a.get("total", 0.0)) + extra_80d + extra_80c
        extracted["chapter_6A"] = chapter_6a
    if extra_other_sources:
        other_income = dict(extracted.get("other_income", {}))
        other_income["other_sources"] = float_safe(other_income.get("other_sources", 0.0)) + extra_other_sources
        other_income["total"] = float_safe(other_income.get("total", 0.0)) + extra_other_sources
        extracted["other_income"] = other_income

    ay_str = extracted.get("assessment_year") or state.get("ay", "AY2026-27")
    if not ay_str.startswith("AY"): ay_str = f"AY{ay_str}"

    try:
        analysis = compute_tax_from_engine_itr2(extracted, ay=ay_str)
        comp = analysis["computed"]

        form.personal_info.assessment_year = ay_str
        form.personal_info.pan = extracted.get("employee_pan")
        form.personal_info.first_name = extracted.get("employee_name")
        form.residential_status_confirmed = residential_status_confirmed

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
        form.salary_income.allowances_exempt_10_13a = val("hra_exemption")
        # total_exemptions (what net_salary is actually computed from) can
        # exceed hra_exemption alone — LTA/10(14)/other allowances the Form 16
        # lumped in without breaking out. Surface the residual so the salary
        # breakdown a filer sees actually sums to taxable_salary instead of
        # leaving an unexplained gap. Mirrors the same fix in itr_graph.py.
        form.salary_income.total_exempt_allowances = val("total_exemptions")
        form.salary_income.allowances_exempt_other = max(0.0, val("total_exemptions") - val("hra_exemption"))
        form.salary_income.standard_deduction_16ia = val("standard_deduction")
        form.salary_income.professional_tax_16iii = val("professional_tax")

        form.house_property_loss_carried_forward = val("house_property_loss_carried_forward")

        cg = comp.get("capital_gains", {}) or {}
        form.capital_gains_summary.stcg_111a = float_safe(cg.get("stcg_111a", 0.0))
        form.capital_gains_summary.ltcg_112a = float_safe(cg.get("ltcg_112a", 0.0))
        form.capital_gains_summary.ltcg_112_other = float_safe(cg.get("ltcg_112_other", 0.0))
        # Non-equity short-term gains: taxed at slab rate (folded into
        # taxable_income, not shown at a special rate below) — without this,
        # this bucket inflates tax_before_rebate with no line item anywhere
        # to explain it, which reads as an unexplained/double-counted figure.
        form.capital_gains_summary.stcg_slab = float_safe(cg.get("stcg_slab", 0.0))
        form.capital_gains_summary.capital_gains_tax = val("capital_gains_tax")

        form.foreign_income = [
            ForeignIncomeEntry(
                country=e.get("country"),
                income_type=e.get("income_type", "other"),
                foreign_income_amount_inr=float_safe(e.get("foreign_income_amount_inr", 0.0)),
                foreign_tax_paid_inr=float_safe(e.get("foreign_tax_paid_inr", 0.0)),
            )
            for e in extracted.get("foreign_income_raw", [])
        ]
        form.foreign_assets = [
            ForeignAssetEntry(
                country=a.get("country"),
                asset_type=a.get("asset_type", "bank_account"),
                peak_value_inr=float_safe(a.get("peak_value", 0.0)),
                closing_value_inr=float_safe(a.get("closing_value", 0.0)),
            )
            for a in foreign_assets_raw
        ]
        form.foreign_tax_credit = val("foreign_tax_credit")

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

        # Form 16's own flat "other sources" figure plus a dedicated
        # other_sources_income doc's savings-interest bucket both land in
        # savings_bank_interest (the 80TTA-eligible line) — the closest
        # existing bucket to a payroll-reported catch-all. Read the ORIGINAL
        # unmutated form16 doc here, not extracted["other_income"] (which
        # already has the full dividends+savings+fd+other total folded in
        # for the engine's gross-income computation above) — using that
        # merged figure here too would double-count the dedicated-doc
        # amounts on top of themselves.
        form16_other_sources = float_safe((form16_doc or {}).get("data", {}).get("other_income", {}).get("other_sources", 0.0))
        form.other_sources.savings_bank_interest = form16_other_sources + _sum_docs(docs, "other_sources_income", "savings_interest")
        form.other_sources.fd_interest = _sum_docs(docs, "other_sources_income", "fd_interest")
        form.other_sources.dividends = _sum_docs(docs, "other_sources_income", "dividends")
        form.other_sources.other_interest = _sum_docs(docs, "other_sources_income", "other_interest")
        form.other_sources.compute()

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

    foreign_income = form.get("foreign_income", [])
    ftc = form.get("foreign_tax_credit", 0.0)
    if foreign_income:
        foreign_total = sum(e.get("foreign_income_amount_inr", 0.0) for e in foreign_income)
        lines.append("")
        lines.append(f"   Foreign income (Schedule FSI, taxed at slab rate like domestic income): Rs {foreign_total:,.0f}")
        if ftc > 0:
            lines.append(f"   Foreign tax credit claimed (Sec 90/91): Rs {ftc:,.0f}")

    lines.append("")
    taxable_income = tax_comp.get("taxable_income", 0.0)
    total_tax = tax_comp.get("total_tax_liability", 0.0)
    lines.append(f"3. Taxable Income (slab-rate portion): Rs {taxable_income:,.0f}")
    lines.append(f"   = **Total Tax Liability: Rs {total_tax:,.0f}**")

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
