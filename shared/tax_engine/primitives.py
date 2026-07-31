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


def round_to_nearest_10(value: float) -> float:
    """Sections 288A/288B's rounding algorithm as a plain scalar function, so
    both the round_statutory primitive and code outside the engine (e.g.
    rounding a refund/payable figure computed after TDS is merged in) can
    share one implementation. Paise are dropped first (truncated, not
    rounded), then the last digit of the whole-rupee amount decides
    direction: five or more rounds up, less than five rounds down.
    """
    whole = math.trunc(value)
    remainder = whole % 10
    rounded = whole - remainder + 10 if remainder >= 5 else whole - remainder
    return float(rounded)


def round_statutory(state: dict, params: dict) -> dict:
    """Sections 288A/288B: round a specified state field to the nearest
    multiple of ten rupees (see round_to_nearest_10). This is an 8th
    primitive beyond the original 7 — a case the brief's own "new mechanism
    needs a new primitive" escape hatch anticipates, not a violation of the
    fixed set.

    params: {"field": "<state key>"} — nearest is always 10 per the statute,
    so it isn't a param; hardcoding the literal legal constant here is the
    correct call, not the kind of hardcoding the config-driven design forbids.

    Per the Act, only two amounts are ever rounded this way: total/taxable
    income (288A) and the final tax payable or refund due (288B) — never
    intermediate sub-heads like slab tax, rebate, surcharge, or cess on
    their own. Callers must only place this primitive at those two config
    checkpoints.
    """
    state = dict(state)
    field = params["field"]
    state[field] = round_to_nearest_10(state.get(field, 0.0))
    return state


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


# ── ITR-2-only primitives ────────────────────────────────────────────────────
# Everything below exists because ITR-2 filers can have income the 7 primitives
# above were never meant to model: multiple house properties, capital gains
# taxed at special rates instead of slab rates, and foreign income eligible for
# DTAA relief. Each is a genuinely new computation mechanism (same rationale
# that justified round_statutory as an 8th primitive), not a variant of an
# existing one — so each gets its own primitive rather than a branch inside
# aggregate_gross_income/apply_slabs/apply_cess, which stay untouched and keep
# serving ITR-1 exactly as before. ITR-2 configs are simply free to place these
# alongside the original 8 in their own `steps` list.
#
# Known simplifications (first-cut scaffold, not a certified compliance
# engine — flagged here rather than left implicit):
#   - Capital loss set-off follows Sections 70/74 only loosely: losses net
#     within their own bucket and short-term loss offsets long-term gain, but
#     inter-year carry-forward is only surfaced as a number, not tracked
#     across filing years.
#   - The indexation/rate transition introduced by the Finance Act 2024 (LTCG
#     on land/building/unlisted shares acquired before 23-Jul-2024 can choose
#     indexed 20% vs unindexed 12.5%) is not modelled — configs pick one rate
#     path and apply it uniformly.
#   - Surcharge here reuses the plain apply_surcharge primitive; the special
#     15% surcharge cap that applies specifically to capital-gains/dividend
#     income regardless of the taxpayer's slab is not separately enforced.


def aggregate_house_properties(state: dict, params: dict) -> dict:
    """Schedule HP for multiple properties (ITR-1's single implicit property
    is handled entirely outside the engine, in itr1_schema.py's own
    HousePropertyIncome.compute() — this primitive is ITR-2's replacement,
    operating on a list instead of one scalar).

    state['house_properties']: list of
        {annual_value, municipal_tax_paid, interest_on_loan_24b, property_type}
    Nets each property (30% standard deduction, self-occupied interest capped
    at Rs 2,00,000 per Sec 24(b) — a fixed statutory figure, hardcoded here
    for the same reason round_to_nearest_10 hardcodes "nearest 10"), sums
    them, then applies the Sec 71(3A) cap: house-property loss set off
    against OTHER heads of income is capped at Rs 2,00,000 in aggregate
    across ALL properties combined, not per property. Anything beyond that
    cap is only surfaced as 'house_property_loss_carried_forward', not
    actually applied this year.

    Writes state['house_property_income'] (the flat number
    aggregate_gross_income already knows how to consume unmodified) plus a
    breakdown for explainability.
    """
    state = dict(state)
    SELF_OCCUPIED_INTEREST_CAP = 200000.0
    AGGREGATE_LOSS_SETOFF_CAP = 200000.0

    properties = state.get("house_properties", [])
    breakdown = []
    total = 0.0
    for prop in properties:
        annual_value = prop.get("annual_value", 0.0)
        municipal_tax = prop.get("municipal_tax_paid", 0.0)
        interest = prop.get("interest_on_loan_24b", 0.0)
        is_self_occupied = prop.get("property_type", "self_occupied") == "self_occupied"

        net_annual_value = max(0.0, annual_value - municipal_tax)
        standard_deduction_30pct = net_annual_value * 0.30
        capped_interest = min(interest, SELF_OCCUPIED_INTEREST_CAP) if is_self_occupied else interest

        income = net_annual_value - standard_deduction_30pct - capped_interest
        breakdown.append({**prop, "net_annual_value": net_annual_value, "income": round(income, 2)})
        total += income

    if total < -AGGREGATE_LOSS_SETOFF_CAP:
        state["house_property_income"] = -AGGREGATE_LOSS_SETOFF_CAP
        state["house_property_loss_carried_forward"] = round(-total - AGGREGATE_LOSS_SETOFF_CAP, 2)
    else:
        state["house_property_income"] = round(total, 2)
        state["house_property_loss_carried_forward"] = 0.0

    state["house_property_breakdown"] = breakdown
    return state


def compute_capital_gains(state: dict, params: dict) -> dict:
    """Schedule CG. Classifies each transaction into a special-rate bucket
    (taxed later by apply_special_rate_capital_gains_tax) or, for gains that
    are taxed at slab rate rather than a special rate, folds them straight
    into 'other_source_income' — the field aggregate_gross_income already
    sums unmodified, so no existing primitive needs to know capital gains
    exist at all.

    state['capital_gains_raw']: list of transactions, each
        {asset_type: "equity_stt" | "other",
         holding_period_months: float,
         sale_value, cost_of_acquisition, improvement_cost, exemption_claimed}
    params: {equity_ltcg_months, other_ltcg_months} — holding period above
    which a gain is long-term; below it, short-term.

    Equity-with-STT short-term gains fall under Sec 111A (special rate);
    non-equity short-term gains are slab-taxed (folded into other_source_income).
    Both equity and non-equity long-term gains get a special rate (112A / 112
    respectively) — kept as separate buckets since 112A alone carries the
    annual exemption threshold.
    """
    state = dict(state)
    equity_ltcg_months = params.get("equity_ltcg_months", 12)
    other_ltcg_months = params.get("other_ltcg_months", 24)

    stcg_111a = stcg_slab = ltcg_112a = ltcg_112_other = 0.0
    breakdown = []

    for txn in state.get("capital_gains_raw", []):
        is_equity = txn.get("asset_type") == "equity_stt"
        holding_months = txn.get("holding_period_months", 0.0)
        ltcg_threshold = equity_ltcg_months if is_equity else other_ltcg_months
        is_long_term = holding_months >= ltcg_threshold

        gain = (
            txn.get("sale_value", 0.0)
            - txn.get("cost_of_acquisition", 0.0)
            - txn.get("improvement_cost", 0.0)
            - txn.get("exemption_claimed", 0.0)
        )

        if is_equity and is_long_term:
            ltcg_112a += gain
        elif is_equity and not is_long_term:
            stcg_111a += gain
        elif not is_equity and is_long_term:
            ltcg_112_other += gain
        else:
            stcg_slab += gain

        breakdown.append({**txn, "is_long_term": is_long_term, "gain": round(gain, 2)})

    # Short-term capital loss may offset long-term gains (Sec 70); long-term
    # loss may only offset other long-term gains. Simplified same-head netting
    # only — not a full Chapter VI-A set-off/carry-forward implementation.
    if stcg_111a < 0:
        ltcg_112_other += stcg_111a
        stcg_111a = 0.0
    if ltcg_112_other < 0:
        ltcg_112a += ltcg_112_other
        ltcg_112_other = 0.0

    state["capital_gains"] = {
        "stcg_111a": round(max(0.0, stcg_111a), 2),
        "ltcg_112a": round(max(0.0, ltcg_112a), 2),
        "ltcg_112_other": round(max(0.0, ltcg_112_other), 2),
    }
    state["capital_gains_breakdown"] = breakdown
    state["other_source_income"] = state.get("other_source_income", 0.0) + stcg_slab
    return state


def apply_special_rate_capital_gains_tax(state: dict, params: dict) -> dict:
    """Tax on Sec 111A/112/112A buckets produced by compute_capital_gains.
    These are taxed at flat rates instead of the slab schedule, so this runs
    independently of apply_slabs and folds its result into 'tax_after_rebate'
    — the same field apply_surcharge/apply_cess already read from — rather
    than requiring either of those primitives to learn a new field name.

    params: {stcg_111a_rate, ltcg_112a_rate, ltcg_112a_exemption, ltcg_112_other_rate}
    Sec 87A rebate does not apply to these buckets, which is why this runs
    AFTER apply_rebate rather than before.
    """
    state = dict(state)
    cg = state.get("capital_gains", {})

    stcg_111a_tax = cg.get("stcg_111a", 0.0) * params.get("stcg_111a_rate", 0.20)
    ltcg_112a_taxable = max(0.0, cg.get("ltcg_112a", 0.0) - params.get("ltcg_112a_exemption", 0.0))
    ltcg_112a_tax = ltcg_112a_taxable * params.get("ltcg_112a_rate", 0.125)
    ltcg_112_other_tax = cg.get("ltcg_112_other", 0.0) * params.get("ltcg_112_other_rate", 0.125)

    capital_gains_tax = round(stcg_111a_tax + ltcg_112a_tax + ltcg_112_other_tax, 2)
    state["capital_gains_tax"] = capital_gains_tax
    state["tax_after_rebate"] = round(state.get("tax_after_rebate", 0.0) + capital_gains_tax, 2)
    return state


def apply_foreign_tax_credit(state: dict, params: dict) -> dict:
    """Sec 90/91 DTAA foreign tax credit relief: the lower of (foreign tax
    actually paid) and (the proportionate share of Indian tax attributable to
    that foreign income). Runs AFTER apply_cess since relief is granted
    against final tax liability including cess (Rule 128), and writes
    'total_tax' directly since it's the last step before rounding.

    state['foreign_income']: {foreign_income_amount, foreign_tax_paid}
    """
    state = dict(state)
    fi = state.get("foreign_income", {})
    foreign_income_amount = fi.get("foreign_income_amount", 0.0)
    foreign_tax_paid = fi.get("foreign_tax_paid", 0.0)

    taxable_income = state.get("taxable_income", 0.0)
    total_tax = state.get("total_tax", 0.0)

    if taxable_income > 0 and foreign_income_amount > 0:
        proportionate_indian_tax = total_tax * (foreign_income_amount / taxable_income)
        relief = round(min(foreign_tax_paid, proportionate_indian_tax), 2)
    else:
        relief = 0.0

    state["foreign_tax_credit_relief"] = relief
    state["total_tax"] = round(max(0.0, total_tax - relief), 2)
    return state
