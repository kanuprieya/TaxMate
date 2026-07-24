"""
AY-agnostic tax computation primitives
=========================================
Each primitive has the signature (state: dict, params: dict) -> dict.

`state` carries the running computation (inputs + everything computed so
far). `params` comes entirely from the per-AY/regime config file. A
primitive must never branch on assessment year or regime directly — any
such distinction belongs in which primitives a config lists and what
params it passes them (see shared/tax_engine/configs/).
"""
from __future__ import annotations
import math


def aggregate_gross_income(state: dict, params: dict) -> dict:
    """Salary (net of exemptions/standard deduction/professional tax) plus
    house property and other-source income, into gross_total_income."""
    state = dict(state)
    gross_salary = state.get("gross_salary", 0.0)
    exempt_allowances = state.get("exempt_allowances", 0.0)
    professional_tax = state.get("professional_tax", 0.0)
    standard_deduction = params.get("standard_deduction", 0.0)

    net_salary = gross_salary - exempt_allowances - standard_deduction - professional_tax
    house_property_income = state.get("house_property_income", 0.0)
    other_source_income = state.get("other_source_income", 0.0)

    state["standard_deduction_applied"] = standard_deduction
    state["net_salary"] = net_salary
    state["gross_total_income"] = net_salary + house_property_income + other_source_income
    return state


def apply_deductions(state: dict, params: dict) -> dict:
    """Chapter VI-A deductions (80C family, 80CCD(1B), 80D, 80TTA/80TTB).

    Old-regime-only in practice — enforced by a config simply omitting this
    step from its `steps` list for the new regime, not by a code branch here.
    """
    state = dict(state)
    deductions = state.get("deductions", {})

    sec_80c_family = min(
        deductions.get("sec_80c", 0.0) + deductions.get("sec_80ccc", 0.0) + deductions.get("sec_80ccd_1", 0.0),
        params.get("sec_80c_cap", math.inf),
    )
    sec_80ccd_1b = min(deductions.get("sec_80ccd_1b", 0.0), params.get("sec_80ccd_1b_cap", math.inf))
    sec_80d = min(deductions.get("sec_80d", 0.0), params.get("sec_80d_cap", math.inf))

    if deductions.get("is_senior"):
        interest_deduction = min(deductions.get("sec_80ttb", 0.0), params.get("sec_80ttb_cap", math.inf))
    else:
        interest_deduction = min(deductions.get("sec_80tta", 0.0), params.get("sec_80tta_cap", math.inf))

    total_deductions = sec_80c_family + sec_80ccd_1b + sec_80d + interest_deduction

    state["capped_deductions"] = {
        "sec_80c_family": sec_80c_family,
        "sec_80ccd_1b": sec_80ccd_1b,
        "sec_80d": sec_80d,
        "interest_deduction": interest_deduction,
    }
    state["total_deductions"] = total_deductions
    return state


def compute_taxable_income(state: dict, params: dict) -> dict:
    state = dict(state)
    gti = state.get("gross_total_income", 0.0)
    total_deductions = state.get("total_deductions", 0.0)
    state["taxable_income"] = max(0.0, gti - total_deductions)
    return state


def apply_slabs(state: dict, params: dict) -> dict:
    """params['slabs']: ascending list of [upper_limit, rate] pairs.
    upper_limit=None on the last pair means infinity."""
    state = dict(state)
    taxable_income = state.get("taxable_income", 0.0)

    tax = 0.0
    prev_limit = 0.0
    for upper_limit, rate in params["slabs"]:
        limit = math.inf if upper_limit is None else upper_limit
        if taxable_income <= prev_limit:
            break
        taxable_in_slab = min(taxable_income, limit) - prev_limit
        tax += taxable_in_slab * rate
        prev_limit = limit

    state["tax_before_rebate"] = round(tax, 2)
    return state


def apply_rebate(state: dict, params: dict) -> dict:
    """Section 87A rebate. params: income_threshold, max_rebate."""
    state = dict(state)
    taxable_income = state.get("taxable_income", 0.0)
    tax_before_rebate = state.get("tax_before_rebate", 0.0)

    if taxable_income <= params.get("income_threshold", 0.0):
        rebate = min(tax_before_rebate, params.get("max_rebate", 0.0))
    else:
        rebate = 0.0

    state["rebate_87a"] = round(rebate, 2)
    state["tax_after_rebate"] = round(max(0.0, tax_before_rebate - rebate), 2)
    return state


def apply_surcharge(state: dict, params: dict) -> dict:
    """params['tiers']: ascending list of [income_threshold, rate]. Applies
    the rate of the highest threshold taxable_income exceeds (0 if none).

    Marginal relief on surcharge is not modelled — a documented
    simplification, not an oversight: incomes that would trigger meaningful
    surcharge (>50L) already fail the ITR-1 eligibility check elsewhere in
    the pipeline, so this primitive mainly exists for completeness.
    """
    state = dict(state)
    taxable_income = state.get("taxable_income", 0.0)
    tiers = params.get("tiers", [])

    rate = 0.0
    for threshold, tier_rate in tiers:
        if taxable_income > threshold:
            rate = tier_rate

    surcharge = round(state.get("tax_after_rebate", 0.0) * rate, 2)
    state["surcharge_rate"] = rate
    state["surcharge"] = surcharge
    return state


def apply_cess(state: dict, params: dict) -> dict:
    """Health & education cess. params: rate (default 4%)."""
    state = dict(state)
    rate = params.get("rate", 0.04)
    base = state.get("tax_after_rebate", 0.0) + state.get("surcharge", 0.0)
    cess = round(base * rate, 2)

    state["health_education_cess"] = cess
    state["total_tax"] = round(base + cess, 2)
    return state
