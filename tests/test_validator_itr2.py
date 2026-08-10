"""
Test harness — ITR2Validator
===============================
Covers the known-scope-gap warning for the pre-23-Jul-2024 land/building
indexation election (see shared/tax_engine/primitives.py's
apply_special_rate_capital_gains_tax docstring for the CBDT AY2026-27
validation-rule citations backing this gap).

Run:
    pytest tests/test_validator_itr2.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.validator_itr2 import ITR2Validator
from shared.validator import TaxConfig

BASE_COMPUTED = {
    "gross_salary": 1000000,
    "standard_deduction": 75000,
    "tds_deducted": 50000,
    "tax_regime": "new",
    "hra_exemption": 0,
    "house_property_loss_carried_forward": 0,
    "gross_total_income": 925000,
    "taxable_income": 925000,
}
BASE_EXTRACTED = {"tax_regime": "new"}


class TestLandBuildingIndexationGapWarning:

    def test_warns_when_non_equity_ltcg_present(self):
        computed = {**BASE_COMPUTED, "capital_gains": {"stcg_111a": 0, "ltcg_112a": 0, "ltcg_112_other": 300000}}
        result = ITR2Validator(TaxConfig(assessment_year=2026)).validate(BASE_EXTRACTED, computed)
        assert any("indexation" in w for w in result.warnings)
        assert any("23-Jul-2024" in w for w in result.warnings)

    def test_no_warning_when_no_non_equity_ltcg(self):
        computed = {**BASE_COMPUTED, "capital_gains": {"stcg_111a": 200000, "ltcg_112a": 1500000, "ltcg_112_other": 0}}
        result = ITR2Validator(TaxConfig(assessment_year=2026)).validate(BASE_EXTRACTED, computed)
        assert not any("indexation" in w for w in result.warnings)

    def test_no_warning_when_no_capital_gains_at_all(self):
        computed = {**BASE_COMPUTED, "capital_gains": {}}
        result = ITR2Validator(TaxConfig(assessment_year=2026)).validate(BASE_EXTRACTED, computed)
        assert not any("indexation" in w for w in result.warnings)
