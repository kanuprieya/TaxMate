"""
ITR-2 validation
===================
Mirrors shared/validator.py's TaxValidator, but for ITR-2's broader income
shape (multiple house properties, capital gains, foreign income) instead of
salary-only. Reuses the ValidationResult/TaxConfig shapes from
shared/validator.py by import (read-only) so both validators plug into their
respective graphs the same way — shared/validator.py itself is not modified.
"""

from typing import Dict, Any
from shared.validator import ValidationResult, TaxConfig


class ITR2Validator:

    REQUIRED_FIELDS = [
        "gross_salary",
        "standard_deduction",
        "tax_regime",
        "tds_deducted",
    ]

    def __init__(self, config: TaxConfig):
        self.config = config

    def validate(self, extracted: Dict[str, Any], computed: Dict[str, Any]) -> ValidationResult:
        result = ValidationResult(status="ok")

        # ---- 1. Required fields ----
        for field in self.REQUIRED_FIELDS:
            val = computed.get(field)
            if val is None or val == "missing":
                result.errors.append(f"Missing mandatory field: {field.replace('_', ' ').title()}")
                result.confidence_score -= 20

        if result.errors:
            result.status = "needs_review"
            return result

        # ---- 2. Basic sanity checks (same as ITR-1) ----
        gross_salary = float(computed.get("gross_salary", 0))
        exemptions = float(computed.get("hra_exemption", 0))
        tds = float(computed.get("tds_deducted", 0))

        if exemptions > gross_salary and gross_salary > 0:
            result.errors.append("Exemptions exceed gross salary")

        if tds > 0.6 * gross_salary and gross_salary > 100000:
            result.warnings.append("TDS unusually high vs salary (over 60%)")

        # ---- 3. Regime consistency ----
        regime = extracted.get("tax_regime", "missing")
        if regime not in ["old", "new", "missing"]:
            result.errors.append("Invalid or missing tax regime")

        if regime == "new":
            chapter_6a = extracted.get("chapter_6A", {})
            if chapter_6a.get("80C", 0) > 0:
                result.warnings.append("Section 80C deductions detected in New Regime (will be ignored)")

        # ---- 4. House property ----
        hp_loss_cf = float(computed.get("house_property_loss_carried_forward", 0))
        if hp_loss_cf < 0:
            result.errors.append("House property loss carried forward cannot be negative")

        # ---- 5. Capital gains sanity ----
        cg = computed.get("capital_gains", {}) or {}
        for bucket in ("stcg_111a", "ltcg_112a", "ltcg_112_other"):
            if float(cg.get(bucket, 0)) < 0:
                result.errors.append(f"Capital gains bucket '{bucket}' computed negative — set-off logic failed")

        # ---- 6. Foreign income disclosure ----
        foreign_tax_paid = 0.0
        for entry in extracted.get("foreign_income", []) or []:
            foreign_tax_paid += float(entry.get("foreign_tax_paid", 0) or 0)
        if foreign_tax_paid > 0 and not extracted.get("foreign_assets"):
            result.warnings.append(
                "Foreign tax credit claimed but no Schedule FA (foreign asset) disclosure found — "
                "FA disclosure is mandatory for residents regardless of income earned."
            )

        # ---- 7. Computation consistency ----
        gross_total_income = float(computed.get("gross_total_income", 0))
        total_income = float(computed.get("taxable_income", 0))
        if total_income > gross_total_income + 5 and gross_total_income > 0:
            result.errors.append("Total taxable income exceeds gross total income")

        # ---- 8. Final scoring ----
        if result.errors:
            result.status = "needs_review"
            result.integrity_score -= 50

        result.confidence_score = max(0, result.confidence_score) / 100.0
        result.integrity_score = max(0, result.integrity_score) / 100.0

        return result
