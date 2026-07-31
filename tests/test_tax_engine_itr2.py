"""
Test harness — ITR-2 primitives/config vs. hand-verified scenarios
=====================================================================
Mirrors tests/test_tax_engine.py's style (hand-verified slab arithmetic in
comments) but exercises the ITR-2-only primitives (aggregate_house_properties,
compute_capital_gains, apply_special_rate_capital_gains_tax,
apply_foreign_tax_credit) and the *_ITR2_*.json configs. Does not modify or
duplicate tests/test_tax_engine.py — this is a new file for a new pipeline.

Run:
    pytest tests/test_tax_engine_itr2.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.tax_engine import compute
from shared.tax_utils_itr2 import compute_tax_from_engine_itr2

AY = "AY2026-27_ITR2"


class TestHousePropertyAggregation:

    def test_single_let_out_property_folds_into_gti(self):
        """
        Gross salary 10,00,000; standard deduction 75,000 -> net salary 9,25,000.
        One let-out property: annual value 3,00,000, municipal tax 20,000
             -> NAV = 2,80,000; 30% standard deduction = 84,000;
             interest 50,000 (uncapped — not self-occupied)
             -> HP income = 2,80,000 - 84,000 - 50,000 = 1,46,000.
        GTI = 9,25,000 + 1,46,000 = 10,71,000 (no capital gains, ends in 0 already).
        Slabs on 10,71,000: 0-4L@0=0, 4-8L@5%=20,000, 8L-10,71,000(2,71,000)@10%=27,100
             -> tax_before_rebate = 47,100.
        Taxable income (10,71,000) < rebate threshold (12L) -> rebate = min(47100,60000)=47,100
             -> tax_after_rebate = 0. No capital gains, no surcharge. Cess on 0 = 0.
        """
        result = compute(AY, "new", {
            "gross_salary": 1000000,
            "house_properties": [{
                "annual_value": 300000,
                "municipal_tax_paid": 20000,
                "interest_on_loan_24b": 50000,
                "property_type": "let_out",
            }],
            "capital_gains_raw": [],
        })
        assert result["house_property_income"] == 146000
        assert result["gross_total_income"] == 1071000
        assert result["taxable_income"] == 1071000
        assert result["total_tax"] == 0

    def test_aggregate_loss_across_properties_capped_at_2_lakh(self):
        """
        Two self-occupied properties, each with interest 3,00,000 (capped at
        2,00,000 per property per Sec 24(b)) and zero annual value (self-
        occupied) -> each property's income = 0 - 0 - 2,00,000 = -2,00,000.
        Combined loss = -4,00,000, but Sec 71(3A) caps loss set-off against
        other income at Rs 2,00,000 in aggregate -> house_property_income
        floors at -2,00,000, and the remaining Rs 2,00,000 is carried forward.
        """
        result = compute(AY, "new", {
            "gross_salary": 0,
            "house_properties": [
                {"annual_value": 0, "municipal_tax_paid": 0, "interest_on_loan_24b": 300000, "property_type": "self_occupied"},
                {"annual_value": 0, "municipal_tax_paid": 0, "interest_on_loan_24b": 300000, "property_type": "self_occupied"},
            ],
            "capital_gains_raw": [],
        })
        assert result["house_property_income"] == -200000
        assert result["house_property_loss_carried_forward"] == 200000


class TestCapitalGains:

    def test_special_rate_buckets_taxed_independently_of_slabs(self):
        """
        Gross salary 1,00,000; standard deduction 75,000 -> net salary 25,000.
        Four capital gains transactions:
          - equity, 6 months held (<12) -> STCG 111A: 5,00,000 - 3,00,000 = 2,00,000
          - equity, 24 months held (>=12) -> LTCG 112A: 20,00,000 - 5,00,000 = 15,00,000
          - non-equity, 6 months held (<24) -> slab-taxed STCG: 1,00,000 - 60,000 = 40,000
            (folded into other_source_income, not a special-rate bucket)
          - non-equity, 30 months held (>=24) -> LTCG 112: 8,00,000 - 5,00,000 = 3,00,000
        GTI = 25,000 (salary) + 0 (HP) + 40,000 (slab-taxed STCG) = 65,000.
        Taxable income 65,000 -> entirely in the 0% slab -> tax_before_rebate=0,
        rebate=0, tax_after_rebate=0.
        Special-rate tax: STCG111A 2,00,000*20% = 40,000.
          LTCG112A (15,00,000 - 1,25,000 exemption) * 12.5% = 13,75,000*0.125 = 1,71,875.
          LTCG112(other) 3,00,000*12.5% = 37,500.
          -> capital_gains_tax = 40,000 + 1,71,875 + 37,500 = 2,49,375.
        tax_after_rebate becomes 2,49,375. No surcharge (well below 50L).
        Cess = 2,49,375 * 4% = 9,975. total_tax = 2,59,350 (already a multiple of 10).
        """
        result = compute(AY, "new", {
            "gross_salary": 100000,
            "house_properties": [],
            "capital_gains_raw": [
                {"asset_type": "equity_stt", "holding_period_months": 6, "sale_value": 500000, "cost_of_acquisition": 300000},
                {"asset_type": "equity_stt", "holding_period_months": 24, "sale_value": 2000000, "cost_of_acquisition": 500000},
                {"asset_type": "other", "holding_period_months": 6, "sale_value": 100000, "cost_of_acquisition": 60000},
                {"asset_type": "other", "holding_period_months": 30, "sale_value": 800000, "cost_of_acquisition": 500000},
            ],
        })
        assert result["capital_gains"] == {"stcg_111a": 200000.0, "ltcg_112a": 1500000.0, "ltcg_112_other": 300000.0}
        assert result["capital_gains_tax"] == 249375.0
        assert result["taxable_income"] == 65000
        assert result["total_tax"] == 259350


class TestForeignTaxCredit:

    def test_relief_capped_at_proportionate_indian_tax(self):
        """
        Gross salary 20,00,000; standard deduction 75,000 -> net salary 19,25,000
             = GTI = taxable income (no HP/CG/deductions).
        Slabs: 0-4L@0=0, 4-8L@5%=20,000, 8-12L@10%=40,000, 12-16L@15%=60,000,
             16L-19,25,000(3,25,000)@20%=65,000 -> tax_before_rebate=1,85,000.
        Taxable income > 12L rebate threshold -> rebate=0. No capital gains,
        no surcharge (<50L). Cess = 1,85,000*4%=7,400 -> total_tax pre-FTC = 1,92,400.
        Foreign income declared: Rs 3,85,000 (exactly 20% of taxable income)
             -> proportionate Indian tax = 1,92,400 * 20% = 38,480.
        Foreign tax actually paid (50,000) exceeds that, so relief is capped
        at the proportionate share: 38,480.
        total_tax = 1,92,400 - 38,480 = 1,53,920 (already a multiple of 10).
        """
        result = compute(AY, "new", {
            "gross_salary": 2000000,
            "house_properties": [],
            "capital_gains_raw": [],
            "foreign_income": {"foreign_income_amount": 385000, "foreign_tax_paid": 50000},
        })
        assert result["foreign_tax_credit_relief"] == 38480.0
        assert result["total_tax"] == 153920


class TestComputeTaxFromEngineItr2:

    def test_form16_shape_mapping_and_refund_matches_direct_engine_call(self):
        """Integration check: compute_tax_from_engine_itr2 must produce the
        same total_tax as the direct engine call for an equivalent input
        (reusing the house-property scenario above), and correctly compute
        refund_or_payable from TDS."""
        extracted = {
            "employee_name": "Test Person",
            "tax_regime": "new",
            "tds": 50000,
            "gross_salary": {"total": 1000000},
            "other_income": {"house_property": 0, "other_sources": 0},
            "chapter_6A": {"80C": 0, "80D": 0},
            "house_properties": [{
                "annual_value": 300000,
                "municipal_tax_paid": 20000,
                "interest_on_loan_24b": 50000,
                "property_type": "let_out",
            }],
            "capital_gains_raw": [],
        }
        result = compute_tax_from_engine_itr2(extracted, ay="AY2026-27")
        comp = result["computed"]
        assert result["status"] == "ok"
        assert comp["house_property_income"] == 146000
        assert comp["total_tax_liability"] == 0
        assert comp["refund_or_payable"] == 50000
