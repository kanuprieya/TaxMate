"""
ITR-2 tax computation glue
=============================
Mirrors shared/tax_utils.py::compute_tax_from_engine, but drives the
*_ITR2_*.json configs and feeds the ITR-2-only primitives (house properties,
capital gains) in addition to the same salary/other-income mapping ITR-1
uses. Foreign income/assets are explicitly out of scope — graph/router.py's
is_out_of_scope() flags and redirects those filings before this function is
ever called, so it has no foreign-income handling to carry.

Reuses shared.tax_utils's private Form16-shape mapper and float_safe by
import (read-only) rather than duplicating it — shared/tax_utils.py itself
is not modified.
"""

from __future__ import annotations

from shared.tax_utils import _extracted_to_engine_inputs, float_safe
from shared.tax_engine import compute as _engine_compute, itr2_ay as _itr2_config_ay


def compute_tax_from_engine_itr2(extracted: dict, ay: str = "AY2026-27") -> dict:
    """Runs the ITR-2 primitive/config tax engine against merged extraction
    data (Form 16 + Schedule HP/CG documents) and reshapes the result into
    the same 'computed' dict shape ITR-1's compute_tax_from_engine produces,
    plus the ITR-2-only fields (capital_gains, house_property_loss_carried_forward)
    so downstream consumers can rely on a superset shape rather than a
    different one.
    """
    raw_name = extracted.get("employee_name") or ""
    if not raw_name.strip():
        return {
            "status": "needs_review",
            "message": "Name not detected from Form 16",
            "computed": {},
            "warnings": [],
            "errors": [{
                "field": "employee_name",
                "severity": "error",
                "message": "Employee name could not be extracted from the uploaded Form 16.",
            }],
            "regime_used": "missing",
            "extracted": extracted,
        }

    regime = extracted.get("tax_regime", "new")
    inputs = _extracted_to_engine_inputs(extracted)
    inputs["house_properties"] = extracted.get("house_properties", [])
    inputs["capital_gains_raw"] = extracted.get("capital_gains_raw", [])

    try:
        state = _engine_compute(_itr2_config_ay(ay), regime, inputs)
    except FileNotFoundError:
        return {
            "status": "needs_review",
            "message": f"No ITR-2 tax rules configured for {ay}",
            "computed": {},
            "warnings": [],
            "errors": [{
                "field": "assessment_year",
                "severity": "error",
                "message": f"No ITR-2 tax config exists for {ay} ({regime} regime) yet. "
                            f"Currently supported: AY2026-27.",
            }],
            "regime_used": "missing",
            "extracted": extracted,
        }

    tds_deducted = float_safe(extracted.get("tds", 0.0))
    total_tax = state.get("total_tax", 0.0)
    from shared.tax_engine import round_to_nearest_10
    refund_or_payable = round_to_nearest_10(tds_deducted - total_tax)

    comp = {
        "gross_salary":        inputs["gross_salary"],
        "salary_income":       state.get("net_salary", 0.0),
        "hra_exemption":       float_safe(extracted.get("hra_exempt", 0.0)),
        "total_exemptions":    inputs["exempt_allowances"],
        "standard_deduction":  state.get("standard_deduction_applied", 0.0),
        "professional_tax":    inputs["professional_tax"],
        "taxable_salary":      state.get("net_salary", 0.0),
        "house_property_income": state.get("house_property_income", 0.0),
        "house_property_loss_carried_forward": state.get("house_property_loss_carried_forward", 0.0),
        "capital_gains":       state.get("capital_gains", {}),
        "capital_gains_tax":   state.get("capital_gains_tax", 0.0),
        "gross_total_income":  state.get("gross_total_income", 0.0),
        "taxable_income":      state.get("taxable_income", 0.0),
        "tax_before_rebate":   state.get("tax_before_rebate", 0.0),
        "rebate_87a":          state.get("rebate_87a", 0.0),
        "tax_after_rebate":    state.get("tax_after_rebate", 0.0),
        "surcharge":           state.get("surcharge", 0.0),
        "cess":                state.get("health_education_cess", 0.0),
        "total_tax_liability": total_tax,
        "tds_deducted":        tds_deducted,
        "refund_or_payable":   refund_or_payable,
        "tax_regime":          regime,
        "notes":               f"Computed via the config-driven ITR-2 tax engine ({ay}, {regime} regime).",
    }
    return {
        "status": "ok",
        "source": "tax_engine_itr2",
        "computed": comp,
        "warnings": [],
        "errors": [],
        "regime_used": regime,
        "extracted": extracted,
    }
