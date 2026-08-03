"""
Test harness — ITR-2 primitives/config vs. hand-verified scenarios
=====================================================================
Mirrors tests/test_tax_engine.py's style (hand-verified slab arithmetic in
comments) but exercises the ITR-2-only primitives (aggregate_house_properties,
compute_capital_gains, apply_special_rate_capital_gains_tax) and the
*_ITR2_*.json configs. Does not modify or duplicate tests/test_tax_engine.py
— this is a new file for a new pipeline. Foreign income/assets are out of
scope by design — see tests/test_router.py for that flag-and-redirect path.

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

    def test_equity_ltcg_fully_within_exemption_is_zero_tax(self):
        """
        Gross salary 1,00,000; standard deduction 75,000 -> net salary 25,000.
        One equity LTCG transaction, 24 months held (>=12):
             3,00,000 - 2,00,000 = 1,00,000 gain -> under the Rs 1,25,000
             Sec 112A exemption threshold entirely.
        GTI = 25,000 (salary) + 0 (HP) + 0 (no slab-taxed gains) = 25,000.
        Taxable income 25,000 -> 0% slab -> tax_before_rebate=0, rebate=0,
        tax_after_rebate=0.
        Special-rate tax: LTCG112A taxable = max(0, 1,00,000-1,25,000) = 0
             -> capital_gains_tax = 0. No surcharge, no cess on zero base.
        total_tax = 0.
        """
        result = compute(AY, "new", {
            "gross_salary": 100000,
            "house_properties": [],
            "capital_gains_raw": [
                {"asset_type": "equity_stt", "holding_period_months": 24, "sale_value": 300000, "cost_of_acquisition": 200000},
            ],
        })
        assert result["capital_gains"] == {"stcg_111a": 0.0, "ltcg_112a": 100000.0, "ltcg_112_other": 0.0}
        assert result["capital_gains_tax"] == 0.0
        assert result["total_tax"] == 0

    def test_special_rate_buckets_taxed_independently_of_slabs(self):
        """
        Covers equity LTCG above the exemption (excess-only taxed), equity
        STCG at the flat rate on the full gain, and non-equity LTCG at the
        flat rate, together in one scenario.

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


class TestCombinedScenario:

    def test_salary_plus_partially_exempt_ltcg_plus_three_house_properties(self):
        """
        Most realistic end-to-end case: salary + one partially-exempt equity
        LTCG + three house properties, one of which is a loss.

        Salary: gross 15,00,000; standard deduction 75,000 -> net salary 14,25,000.

        House properties:
          P1 (let-out): annual value 4,00,000, municipal tax 20,000
               -> NAV 3,80,000; 30% std ded 1,14,000; interest 50,000 (uncapped)
               -> income = 3,80,000 - 1,14,000 - 50,000 = 2,16,000.
          P2 (self-occupied, a loss): annual value 0, interest 1,80,000
               (under the Rs 2,00,000 Sec 24(b) cap) -> income = -1,80,000.
          P3 (let-out): annual value 2,50,000, municipal tax 10,000
               -> NAV 2,40,000; 30% std ded 72,000; interest 30,000 (uncapped)
               -> income = 2,40,000 - 72,000 - 30,000 = 1,38,000.
          Combined = 2,16,000 - 1,80,000 + 1,38,000 = 1,74,000 (positive, so
          the Sec 71(3A) Rs 2,00,000 loss-set-off cap never engages here).

        Capital gains: one equity LTCG, 24 months held:
             9,00,000 - 5,00,000 = 4,00,000 gain -> taxable excess over the
             Rs 1,25,000 exemption = 2,75,000 -> tax = 2,75,000*12.5% = 34,375.

        GTI = 14,25,000 (salary) + 1,74,000 (HP) + 0 (no slab-taxed gains)
             = 15,99,000 = taxable income (new regime, no deductions, already
             a multiple of 10).
        Slabs on 15,99,000: 0-4L@0=0, 4-8L@5%=20,000, 8-12L@10%=40,000,
             12L-15,99,000(3,99,000)@15%=59,850 -> tax_before_rebate=1,19,850.
        Taxable income > 12L rebate threshold -> rebate=0 -> tax_after_rebate=1,19,850.
        + capital_gains_tax 34,375 -> 1,54,225. No surcharge (well below 50L).
        Cess = 1,54,225*4% = 6,169 -> raw total = 1,60,394.
        288B: last digit 4 -> rounds down -> total_tax = 1,60,390.
        """
        result = compute(AY, "new", {
            "gross_salary": 1500000,
            "house_properties": [
                {"annual_value": 400000, "municipal_tax_paid": 20000, "interest_on_loan_24b": 50000, "property_type": "let_out"},
                {"annual_value": 0, "municipal_tax_paid": 0, "interest_on_loan_24b": 180000, "property_type": "self_occupied"},
                {"annual_value": 250000, "municipal_tax_paid": 10000, "interest_on_loan_24b": 30000, "property_type": "let_out"},
            ],
            "capital_gains_raw": [
                {"asset_type": "equity_stt", "holding_period_months": 24, "sale_value": 900000, "cost_of_acquisition": 500000},
            ],
        })
        assert result["house_property_income"] == 174000
        assert result["house_property_loss_carried_forward"] == 0.0
        assert result["capital_gains"] == {"stcg_111a": 0.0, "ltcg_112a": 400000.0, "ltcg_112_other": 0.0}
        assert result["capital_gains_tax"] == 34375.0
        assert result["taxable_income"] == 1599000
        assert result["total_tax"] == 160390


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
