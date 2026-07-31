"""
Test harness — primitive/config tax engine vs. hand-verified scenarios
=========================================================================
Each scenario's expected numbers are computed by hand in the comments
below (plain slab arithmetic) so they can be independently re-checked
without relying on any other part of this codebase.

Run:
    pytest tests/test_tax_engine.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.tax_engine import compute

AY = "AY2026-27"


class TestNewRegime:

    def test_exactly_at_rebate_boundary_is_zero_tax(self):
        """
        Gross salary 12,75,000; standard deduction 75,000 -> taxable = 12,00,000.
        Slabs: 0-4L@0=0, 4-8L@5%=20,000, 8-12L@10%=40,000 -> tax_before_rebate=60,000.
        Taxable income == rebate threshold (12L) -> rebate = min(60000, 60000) = 60,000.
        Tax after rebate = 0. No surcharge (well below 50L). Cess on 0 = 0.
        Expected total_tax = 0.
        """
        result = compute(AY, "new", {"gross_salary": 1275000})
        assert result["taxable_income"] == 1200000
        assert result["tax_before_rebate"] == 60000
        assert result["rebate_87a"] == 60000
        assert result["total_tax"] == 0.0

    def test_above_rebate_threshold_full_slab_tax(self):
        """
        Gross salary 15,75,000; standard deduction 75,000 -> taxable = 15,00,000.
        Slabs: 0-4L@0=0, 4-8L@5%=20,000, 8-12L@10%=40,000, 12-15L@15%=45,000
             -> tax_before_rebate = 1,05,000.
        Taxable income (15L) > threshold (12L) -> rebate = 0.
        No surcharge (well below 50L). Cess = 1,05,000 * 4% = 4,200.
        Expected total_tax = 1,05,000 + 4,200 = 1,09,200.
        (Matches the publicly cited AY2026-27/FY2025-26 new-regime example for a
        ₹15L taxable income.)
        """
        result = compute(AY, "new", {"gross_salary": 1575000})
        assert result["taxable_income"] == 1500000
        assert result["tax_before_rebate"] == 105000
        assert result["rebate_87a"] == 0.0
        assert result["health_education_cess"] == 4200
        assert result["total_tax"] == 109200

    def test_surcharge_tier_triggers_above_50L(self):
        """
        Gross salary 60,75,000; standard deduction 75,000 -> taxable = 60,00,000.
        Slabs: 0-4L@0=0, 4-8L@5%=20,000, 8-12L@10%=40,000, 12-16L@15%=60,000,
               16-20L@20%=80,000, 20-24L@25%=1,00,000, >24L@30% of 36,00,000=10,80,000
             -> tax_before_rebate = 13,80,000.
        Taxable income (60L) > rebate threshold (12L) -> rebate = 0.
        Taxable income (60L) > first surcharge tier (50L) but not the next (1Cr)
             -> surcharge rate = 10% -> surcharge = 13,80,000 * 0.10 = 1,38,000.
        Cess = (13,80,000 + 1,38,000) * 4% = 60,720.
        Expected total_tax = 13,80,000 + 1,38,000 + 60,720 = 15,78,720.

        (Income this high would actually fail ITR-1's own >50L eligibility check
        at the validation node — that gate doesn't exist yet. This test only
        proves apply_surcharge itself fires correctly; it is not a realistic
        ITR-1 filing scenario.)
        """
        result = compute(AY, "new", {"gross_salary": 6075000})
        assert result["taxable_income"] == 6000000
        assert result["tax_before_rebate"] == 1380000
        assert result["rebate_87a"] == 0.0
        assert result["surcharge_rate"] == 0.10
        assert result["surcharge"] == 138000
        assert result["health_education_cess"] == 60720
        assert result["total_tax"] == 1578720


class TestStatutoryRounding:

    def test_taxable_income_and_total_tax_round_to_nearest_10(self):
        """
        Sections 288A/288B: taxable income rounds to the nearest ₹10 (applied
        BEFORE slabs run), and the final tax payable rounds to the nearest
        ₹10 (applied AFTER cess) — intermediate sub-heads (slab tax, rebate,
        cess) are never individually rounded.

        Gross salary 16,50,007; standard deduction 75,000
             -> raw taxable income = 15,75,007.
        288A: last digit 7 -> rounds UP -> taxable income = 15,75,010.

        Slabs on 15,75,010: 0-4L@0=0, 4-8L@5%=20,000, 8-12L@10%=40,000,
             12L-15,75,010 (3,75,010)@15%=56,251.50
             -> tax_before_rebate = 1,16,251.50.
        Taxable income (15,75,010) > rebate threshold (12L) -> rebate = 0.
        No surcharge. Cess = 1,16,251.50 * 4% = 4,650.06.
        Raw total = 1,16,251.50 + 4,650.06 = 1,20,901.56.
        288B: drop paise -> 1,20,901; last digit 1 -> rounds DOWN -> 1,20,900.
        """
        result = compute(AY, "new", {"gross_salary": 1650007})
        assert result["taxable_income"] == 1575010
        assert result["total_tax"] == 120900

    def test_refund_or_payable_also_rounds_to_nearest_10(self):
        """
        288B covers "any amount payable and any refund due" — the net figure
        after TDS is rounded too, not just total_tax on its own.

        Reusing the scenario above (total_tax = 1,20,900 after rounding).
        TDS = 1,25,003 -> raw refund = 1,25,003 - 1,20,900 = 4,103.
        Last digit 3 -> rounds DOWN -> refund = 4,100.
        """
        from shared.tax_utils import compute_tax_from_engine

        extracted = {
            "employee_name": "Test Person",
            "tax_regime": "new",
            "tds": 125003,
            "gross_salary": {"total": 1650007},
            "other_income": {"house_property": 0, "other_sources": 0},
            "chapter_6A": {"80C": 0, "80D": 0},
        }
        result = compute_tax_from_engine(extracted, ay=AY)
        comp = result["computed"]
        assert comp["total_tax_liability"] == 120900
        assert comp["refund_or_payable"] == 4100


class TestOldRegime:

    def test_deductions_capped_no_rebate(self):
        """
        Gross salary 10,00,000, professional tax 2,500, standard deduction 50,000
             -> gross_total_income = 10,00,000 - 50,000 - 2,500 = 9,47,500.
        Deductions: 80C 1,50,000 (at cap), 80D 25,000 (at cap) -> total = 1,75,000.
        Taxable income = 9,47,500 - 1,75,000 = 7,72,500.
        Slabs: 0-2.5L@0=0, 2.5-5L@5%=12,500, 5-7.725L@20% of 2,72,500=54,500
             -> tax_before_rebate = 67,000.
        Taxable income (7,72,500) > rebate threshold (5L) -> rebate = 0.
        No surcharge. Cess = 67,000 * 4% = 2,680.
        Expected total_tax = 67,000 + 2,680 = 69,680.
        """
        result = compute(AY, "old", {
            "gross_salary": 1000000,
            "professional_tax": 2500,
            "deductions": {"sec_80c": 150000, "sec_80d": 25000},
        })
        assert result["gross_total_income"] == 947500
        assert result["total_deductions"] == 175000
        assert result["taxable_income"] == 772500
        assert result["tax_before_rebate"] == 67000
        assert result["rebate_87a"] == 0.0
        assert result["total_tax"] == 69680

    def test_80c_overcontribution_capped_and_rebate_zeroes_tax(self):
        """
        Gross salary 6,00,000, standard deduction 50,000 -> GTI = 5,50,000.
        80C contribution of 2,00,000 exceeds the 1,50,000 cap -> capped to 1,50,000.
        Taxable income = 5,50,000 - 1,50,000 = 4,00,000.
        Slabs: 0-2.5L@0=0, 2.5-4L@5% of 1,50,000=7,500 -> tax_before_rebate=7,500.
        Taxable income (4L) <= rebate threshold (5L) -> rebate = min(7500, 12500) = 7,500.
        Tax after rebate = 0. Expected total_tax = 0.
        """
        result = compute(AY, "old", {
            "gross_salary": 600000,
            "deductions": {"sec_80c": 200000},
        })
        assert result["capped_deductions"]["sec_80c_family"] == 150000
        assert result["taxable_income"] == 400000
        assert result["tax_before_rebate"] == 7500
        assert result["rebate_87a"] == 7500
        assert result["total_tax"] == 0.0
