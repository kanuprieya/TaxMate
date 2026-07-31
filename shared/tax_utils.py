import math

# Standard configuration for AY 2026-27 (FY 2025-26)
CONFIG_AY_2026_27 = {
    "standard_deduction_new_regime": 75000,
    "standard_deduction_old_regime": 50000,
    "rebate_limit_new_regime": 1200000,
    "rebate_limit_old_regime": 500000,
    "rebate_max_new_regime": 60000,
    "rebate_max_old_regime": 12500,
    "sec_80c_limit": 150000,
    "sec_80ccd_1b_limit": 50000,
    "sec_80tta_limit": 10000,
    "sec_80ttb_limit": 50000,
}

METRO_CITIES = ["delhi", "mumbai", "kolkata", "chennai"]

def get_config(ay: str = "AY2026-27"):
    return CONFIG_AY_2026_27

def compute_tax_on_slabs(income: float, slabs: list) -> float:
    tax = 0.0
    prev_limit = 0
    for limit, rate in slabs:
        if income <= prev_limit:
            break
        taxable_in_slab = min(income, limit) - prev_limit
        tax += taxable_in_slab * rate
        prev_limit = limit
    return tax

def compute_tax(taxable_income: float, regime: str = "new", ay: str = "AY2026-27") -> dict:
    cfg = get_config(ay)
    taxable_income = max(0.0, taxable_income)
    
    if regime == "new":
        slabs = [
            (400000,  0.00),
            (800000,  0.05),
            (1200000, 0.10),
            (1600000, 0.15),
            (2000000, 0.20),
            (2400000, 0.25),
            (math.inf, 0.30),
        ]
        rebate_limit = cfg["rebate_limit_new_regime"]
        rebate_max = cfg["rebate_max_new_regime"]
    else:
        slabs = [
            (250000,  0.00),
            (500000,  0.05),
            (1000000, 0.20),
            (math.inf, 0.30),
        ]
        rebate_limit = cfg["rebate_limit_old_regime"]
        rebate_max = cfg["rebate_max_old_regime"]

    tax_before_rebate = compute_tax_on_slabs(taxable_income, slabs)
    rebate_87a = min(tax_before_rebate, rebate_max) if taxable_income <= rebate_limit else 0.0
    tax_after_rebate = max(0.0, tax_before_rebate - rebate_87a)
    
    cess = tax_after_rebate * 0.04
    total = tax_after_rebate + cess
    
    return {
        "tax_before_rebate":      round(tax_before_rebate, 2),
        "rebate_87a":             round(rebate_87a, 2),
        "tax_after_rebate":       round(tax_after_rebate, 2),
        "health_education_cess":  round(cess, 2),
        "total_tax":             round(total, 2),
    }

def require(field: str, data: dict):
    val = data.get(field)
    if val is None or val == "missing" or val == "":
        raise ValueError(f"Mandatory field '{field}' is missing.")
    return val

def float_safe(v):
    if v == "missing" or v is None: return 0.0
    try: return float(v)
    except: return 0.0

# -----------------------------
# Real tax computation, via the config-driven primitive engine
# (shared/tax_engine). Replaces the old name-keyed hardcoded lookup —
# this works for any taxpayer, not just three fixture names.
# -----------------------------
from shared.tax_engine import compute as _engine_compute, round_to_nearest_10


def _extracted_to_engine_inputs(extracted: dict) -> dict:
    """Maps doc-parser's Form16 extraction shape (see doc-parser/parsers/form16.py
    ::form16_to_dict) onto the flat input shape shared.tax_engine primitives expect.

    Standard deduction is deliberately NOT taken from the extracted document —
    the engine always applies the AY config's canonical amount, since a Form 16
    can reflect a stale figure from outdated employer payroll software.
    """
    gross_salary = extracted.get("gross_salary", {})
    other_income = extracted.get("other_income", {})
    chapter_6a   = extracted.get("chapter_6A", {})

    return {
        "gross_salary":          float_safe(gross_salary.get("total", 0.0)),
        "exempt_allowances":     float_safe(extracted.get("total_exemptions", 0.0)),
        "professional_tax":      float_safe(extracted.get("professional_tax", 0.0)),
        "house_property_income": float_safe(other_income.get("house_property", 0.0)),
        "other_source_income":   float_safe(other_income.get("other_sources", 0.0)),
        "deductions": {
            "sec_80c": float_safe(chapter_6a.get("80C", 0.0)),
            "sec_80d": float_safe(chapter_6a.get("80D", 0.0)),
        },
    }


def compute_tax_from_engine(extracted: dict, ay: str = "AY2026-27") -> dict:
    """Runs the real primitive/config tax engine against extracted Form 16 data
    and reshapes its output into the same 'computed' dict shape the rest of the
    pipeline (agent-orchestrator's LangGraph nodes) already expects."""
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

    try:
        state = _engine_compute(ay, regime, inputs)
    except FileNotFoundError:
        return {
            "status": "needs_review",
            "message": f"No tax rules configured for {ay}",
            "computed": {},
            "warnings": [],
            "errors": [{
                "field": "assessment_year",
                "severity": "error",
                "message": f"No tax config exists for {ay} ({regime} regime) yet. "
                            f"Currently supported: AY2026-27.",
            }],
            "regime_used": "missing",
            "extracted": extracted,
        }

    tds_deducted = float_safe(extracted.get("tds", 0.0))
    total_tax = state.get("total_tax", 0.0)
    # Section 288B covers "any amount payable and any refund due" — the net
    # figure after TDS is itself rounded to the nearest ₹10, same as total_tax.
    refund_or_payable = round_to_nearest_10(tds_deducted - total_tax)  # positive = refund, negative = payable

    comp = {
        "gross_salary":        inputs["gross_salary"],
        "salary_income":       state.get("net_salary", 0.0),
        "hra_exemption":       float_safe(extracted.get("hra_exempt", 0.0)),
        "total_exemptions":    inputs["exempt_allowances"],
        "standard_deduction":  state.get("standard_deduction_applied", 0.0),
        "professional_tax":    inputs["professional_tax"],
        "taxable_salary":      state.get("net_salary", 0.0),
        "gross_total_income":  state.get("gross_total_income", 0.0),
        "taxable_income":      state.get("taxable_income", 0.0),
        "tax_before_rebate":   state.get("tax_before_rebate", 0.0),
        "rebate_87a":          state.get("rebate_87a", 0.0),
        "tax_after_rebate":    state.get("tax_after_rebate", 0.0),
        "cess":                state.get("health_education_cess", 0.0),
        "total_tax_liability": total_tax,
        "tds_deducted":        tds_deducted,
        "refund_or_payable":   refund_or_payable,
        "tax_regime":          regime,
        "notes":               f"Computed via the config-driven tax engine ({ay}, {regime} regime).",
    }
    return {
        "status": "ok",
        "source": "tax_engine",
        "computed": comp,
        "warnings": [],
        "errors": [],
        "regime_used": regime,
        "extracted": extracted,
    }

def enforce_deduction_limits(deductions: dict, ay: str = "AY2026-27") -> dict:
    """Caps Chapter VI-A deductions according to statutory limits."""
    cfg = get_config(ay)
    capped = deductions.copy()
    
    # Cap 80C family
    sec_80c_total = float_safe(deductions.get("80C", 0.0)) + float_safe(deductions.get("80CCC", 0.0)) + float_safe(deductions.get("80CCD1", 0.0))
    capped["80C_capped"] = min(sec_80c_total, cfg["sec_80c_limit"])
    
    # Cap 80CCD(1B)
    capped["80CCD_1B_capped"] = min(float_safe(deductions.get("80CCD1B", 0.0)), cfg["sec_80ccd_1b_limit"])
    
    # Cap 80TTA/TTB
    if deductions.get("is_senior"):
        capped["80TTB_capped"] = min(float_safe(deductions.get("80TTB", 0.0)), cfg["sec_80ttb_limit"])
    else:
        capped["80TTA_capped"] = min(float_safe(deductions.get("80TTA", 0.0)), cfg["sec_80tta_limit"])
        
    return capped

def compare_regimes(extracted: dict, ay: str = "AY2026-27") -> dict:
    """Computes tax under both regimes and recommends the best one."""
    import copy
    
    # Compute New Regime
    ext_new = copy.deepcopy(extracted)
    ext_new["tax_regime"] = "new"
    res_new = compute_tax_from_engine(ext_new, ay)

    # Compute Old Regime
    ext_old = copy.deepcopy(extracted)
    ext_old["tax_regime"] = "old"
    res_old = compute_tax_from_engine(ext_old, ay)
    
    tax_new = float_safe(res_new["computed"].get("total_tax_liability"))
    tax_old = float_safe(res_old["computed"].get("total_tax_liability"))
    
    best = "new" if tax_new <= tax_old else "old"
    savings = abs(tax_new - tax_old)
    
    return {
        "best_regime": best,
        "savings": savings,
        "new_regime_tax": tax_new,
        "old_regime_tax": tax_old,
        "new_regime_result": res_new,
        "old_regime_result": res_old
    }
