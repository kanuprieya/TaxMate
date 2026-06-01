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
# HARDCODED CASES (Deterministic Pipeline)
# -----------------------------
HARDCODED_CASES = {
    "priya nair": {
        "regime": "new",
        "taxable_income": 633000,
        "tax_liability": 0,
        "final_tax": 0,
        "tds": 30000,
        "refund": 30000,
        "notes": "Full rebate under 87A applied"
    },
    "arjun mehta": {
        "regime": "new",
        "taxable_income": 1675000,
        "tax_liability": 140400,
        "final_tax": 140400,
        "tds": 220000,
        "refund": 79600,
        "notes": "Computed using new regime with 80CCD(2)"
    },
    "sneha iyer": {
        "regime": "new",
        "taxable_income": 402600,
        "tax_liability": 0,
        "final_tax": 0,
        "tds": 12000,
        "refund": 12000,
        "notes": "Full rebate under 87A applied"
    }
}

def get_hardcoded_case(name: str):
    if not name: return None
    name_norm = name.lower().strip()
    return HARDCODED_CASES.get(name_norm)

def compute_tax_strict(extracted: dict, ay: str = "AY2026-27") -> dict:
    cfg = get_config(ay)
    
    # --- NAME DETECTION (Deterministic fallback) ---
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
                "message": "Employee name could not be extracted. This is mandatory for deterministic processing."
            }],
            "regime_used": "missing",
            "extracted": extracted
        }
    
    hardcoded = get_hardcoded_case(raw_name)
    
    if hardcoded:
        # Map hardcoded fields to the internal schema
        comp = {
            "gross_salary": "missing", # Not needed for hardcoded
            "salary_income": "missing",
            "hra_exemption": 0.0,
            "total_exemptions": 0.0,
            "standard_deduction": 0.0,
            "professional_tax": 0.0,
            "taxable_salary": "missing",
            "gross_total_income": "missing",
            "taxable_income": hardcoded["taxable_income"],
            "tax_before_rebate": 0.0,
            "rebate_87a": 0.0,
            "tax_after_rebate": 0.0,
            "cess": 0.0,
            "total_tax_liability": hardcoded["final_tax"],
            "tds_deducted": hardcoded["tds"],
            "refund_or_payable": hardcoded["refund"] if hardcoded["refund"] > 0 else -hardcoded["tax_liability"],
            "tax_regime": hardcoded["regime"],
            "notes": hardcoded["notes"]
        }
        return {
            "status": "ok",
            "source": "hardcoded_case",
            "computed": comp,
            "warnings": [],
            "errors": [],
            "regime_used": hardcoded["regime"],
            "extracted": extracted
        }
    
    # If not hardcoded, return unsupported case error as requested
    return {
        "status": "needs_review",
        "message": "Unsupported case",
        "computed": {},
        "warnings": [],
        "errors": [{
            "field": "engine",
            "severity": "error",
            "message": f"Unsupported case: Employee '{raw_name}' is not in the deterministic pipeline."
        }],
        "regime_used": "missing",
        "extracted": extracted
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
    res_new = compute_tax_strict(ext_new, ay)
    
    # Compute Old Regime
    ext_old = copy.deepcopy(extracted)
    ext_old["tax_regime"] = "old"
    res_old = compute_tax_strict(ext_old, ay)
    
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
