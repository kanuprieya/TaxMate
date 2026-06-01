from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


# -----------------------------
# CONFIG (AY-aware rules)
# -----------------------------
@dataclass
class TaxConfig:
    assessment_year: int

    def standard_deduction(self):
        # Rule for AY 2026-27 (FY 2025-26) and onwards
        if self.assessment_year >= 2026:
            return 75000
        return 50000


# -----------------------------
# RESULT STRUCTURE
# -----------------------------
@dataclass
class ValidationResult:
    status: str  # "ok" | "needs_review"
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    confidence_score: float = 100.0
    integrity_score: float = 100.0


# -----------------------------
# CORE VALIDATOR
# -----------------------------
class TaxValidator:

    REQUIRED_FIELDS = [
        "gross_salary",
        "standard_deduction",
        "tax_regime",
        "tds_deducted"
    ]

    def __init__(self, config: TaxConfig):
        self.config = config

    # -------------------------
    # FAIL-FAST REQUIRE
    # -------------------------
    def require(self, field: str, data: Dict[str, Any]):
        if field not in data or data[field] is None:
            raise ValueError(f"Missing required field: {field}")
        return data[field]

    # -------------------------
    # MAIN VALIDATION
    # -------------------------
    def validate(self, extracted: Dict[str, Any], computed: Dict[str, Any]) -> ValidationResult:
        result = ValidationResult(status="ok")

        # ---- 1. Required fields ----
        # Note: We check 'computed' because it's the post-processed SSOT
        for field in self.REQUIRED_FIELDS:
            val = computed.get(field)
            if val is None or val == "missing":
                result.errors.append(f"Missing mandatory field: {field.replace('_', ' ').title()}")
                result.confidence_score -= 20

        if result.errors:
            result.status = "needs_review"
            return result

        # ---- 2. Basic sanity checks ----
        gross_salary = float(computed.get("gross_salary", 0))
        exemptions = float(computed.get("hra_exemption", 0))
        tds = float(computed.get("tds_deducted", 0))

        if exemptions > gross_salary and gross_salary > 0:
            result.errors.append("Exemptions exceed gross salary")
        
        if tds > 0.6 * gross_salary and gross_salary > 100000:
            result.warnings.append("TDS unusually high vs salary (over 60%)")

        # ---- 3. HRA correctness ----
        # If we have HRA received in extracted data, compare it
        hra_received = float(extracted.get("hra_received", 0))
        if exemptions > hra_received and hra_received > 0:
            result.errors.append(f"HRA exemption (₹{exemptions:,.0f}) exceeds HRA received (₹{hra_received:,.0f})")

        # ---- 4. Standard deduction check ----
        expected_std = self.config.standard_deduction()
        actual_std = float(computed.get("standard_deduction", 0))
        if actual_std != expected_std and actual_std > 0:
            result.warnings.append(
                f"Standard deduction mismatch (expected ₹{expected_std:,.0f}, found ₹{actual_std:,.0f})"
            )
            result.confidence_score -= 5

        # ---- 5. Regime consistency ----
        regime = extracted.get("tax_regime", "missing")

        if regime not in ["old", "new", "missing"]:
            result.errors.append("Invalid or missing tax regime")

        if regime == "new":
            # In new regime, some deductions are allowed but many aren't.
            # Here we check if 80C was claimed despite being in NEW regime
            chapter_6a = extracted.get("chapter_6A", {})
            if chapter_6a.get("80C", 0) > 0:
                 result.warnings.append("Section 80C deductions detected in New Regime (will be ignored)")

        # ---- 6. Computation consistency ----
        gross_total_income = float(computed.get("gross_total_income", 0))
        total_income = float(computed.get("taxable_income", 0))

        if total_income > gross_total_income and gross_total_income > 0:
            result.errors.append("Total taxable income exceeds gross total income")

        # ---- 7. Form 16 reconciliation ----
        form16_total = float(extracted.get("total_taxable_income", 0))
        if form16_total > 0:
            diff = abs(form16_total - total_income)
            if diff > 10: # Allow small rounding diffs
                result.warnings.append(
                    f"Mismatch with Form 16 total income (diff=₹{diff:,.0f})"
                )
                result.confidence_score -= 10

        # ---- 8. Illegal states ----
        if float(computed.get("taxable_salary", 0)) == 0 and gross_salary > 100000:
            result.errors.append("Taxable salary is zero despite high gross salary")

        if float(computed.get("total_tax_liability", 0)) == 0 and gross_salary > 1200000:
            # Under new regime, tax is zero up to 12L in some cases, but 12L is high
            result.warnings.append("Zero tax calculated on income > ₹12L — verification recommended")

        # ---- 9. Final scoring ----
        if result.errors:
            result.status = "needs_review"
            result.integrity_score -= 50

        result.confidence_score = max(0, result.confidence_score) / 100.0
        result.integrity_score = max(0, result.integrity_score) / 100.0

        return result
