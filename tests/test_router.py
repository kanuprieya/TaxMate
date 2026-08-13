"""
Test harness — ITR-1/ITR-2 eligibility router
=================================================
Covers graph/router.py: the sole place ITR1-vs-ITR2 and in-scope-vs-out-of-
scope decisions live. Verified against CBDT Notification No. 45/2026 (see
router.py's module docstring) rather than assumed from general knowledge.

Run:
    pytest tests/test_router.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agent-orchestrator"))

from graph.router import determine_form_type, is_out_of_scope, run_pipeline


def _form16(gross=1000000):
    return {"doc_type": "form16", "data": {
        "employee_name": "Test Person",
        "employee_pan": "ABCDE1234F",
        "tax_regime": "new",
        "tds": 0,
        "assessment_year": "AY2026-27",
        "gross_salary": {"total": gross},
        "other_income": {"house_property": 0, "other_sources": 0},
        "chapter_6A": {"80C": 0, "80D": 0},
    }}


def _properties(n):
    return {"doc_type": "property", "data": {"house_properties": [{"annual_value": 100000} for _ in range(n)]}}


def _capital_gains(txns):
    return {"doc_type": "capital_gains", "data": {"capital_gains_raw": txns}}


def _foreign_income(country="USA"):
    return {"doc_type": "foreign_income", "data": {
        "foreign_income": [{"country": country, "income_type": "other",
                             "foreign_income_amount_inr": 100000, "foreign_tax_paid_inr": 10000}],
        "foreign_assets": [],
    }}


def _non_resident_status(status="non_resident"):
    return {"doc_type": "residential_status", "data": {"status": status, "days_in_india_current_year": 45}}


class TestDetermineFormType:

    def test_form16_only_is_itr1(self):
        assert determine_form_type([_form16()]) == "itr1"

    def test_two_house_properties_still_itr1(self):
        """AY 2026-27 relaxed the old one-property limit to two."""
        assert determine_form_type([_form16(), _properties(2)]) == "itr1"

    def test_three_house_properties_is_itr2(self):
        assert determine_form_type([_form16(), _properties(3)]) == "itr2"

    def test_small_ltcg_112a_within_exemption_is_still_itr1(self):
        """LTCG u/s 112A up to Rs 1,25,000 is itself ITR-1-eligible per
        AY 2026-27 rules — gain here is 300000-200000=100000, under the cap."""
        txns = [{"asset_type": "equity_stt", "holding_period_months": 24,
                  "sale_value": 300000, "cost_of_acquisition": 200000}]
        assert determine_form_type([_form16(), _capital_gains(txns)]) == "itr1"

    def test_ltcg_112a_above_exemption_is_itr2(self):
        """Gain here is 400000-200000=200000, over the Rs 1,25,000 cap."""
        txns = [{"asset_type": "equity_stt", "holding_period_months": 24,
                  "sale_value": 400000, "cost_of_acquisition": 200000}]
        assert determine_form_type([_form16(), _capital_gains(txns)]) == "itr2"

    def test_any_stcg_111a_is_itr2(self):
        """STCG u/s 111A has no ITR-1-eligible allowance at all, unlike 112A."""
        txns = [{"asset_type": "equity_stt", "holding_period_months": 6,
                  "sale_value": 150000, "cost_of_acquisition": 100000}]
        assert determine_form_type([_form16(), _capital_gains(txns)]) == "itr2"

    def test_total_income_over_50l_is_itr2(self):
        assert determine_form_type([_form16(gross=6000000)]) == "itr2"

    def test_total_income_at_50l_is_still_itr1(self):
        assert determine_form_type([_form16(gross=5000000)]) == "itr1"

    def test_foreign_income_forces_itr2_regardless_of_other_thresholds(self):
        """Real CBDT rule, not just an app-scope choice — ITR-1 is legally
        unavailable to a filer with any foreign income/asset disclosure,
        even a tiny amount well under every other ITR-1 threshold."""
        assert determine_form_type([_form16(gross=500000), _foreign_income()]) == "itr2"


class TestIsOutOfScope:

    def test_no_foreign_docs_is_in_scope(self):
        assert is_out_of_scope([_form16(), _properties(3)]) is None

    def test_resident_foreign_income_is_in_scope(self):
        """Foreign income for a Resident filer is now computed (Schedule
        FSI), not redirected — only residency status (checked separately
        below) still blocks."""
        assert is_out_of_scope([_form16(), _foreign_income("Germany")]) is None

    def test_non_resident_status_is_out_of_scope(self):
        message = is_out_of_scope([_form16(), _non_resident_status("non_resident")])
        assert message is not None
        assert "professional" in message
        assert "Non-Resident" in message

    def test_rnor_status_is_out_of_scope(self):
        message = is_out_of_scope([_form16(), _non_resident_status("rnor")])
        assert message is not None
        assert "RNOR" in message


class TestRunPipelineEndToEnd:

    def test_non_resident_short_circuits_before_either_graph(self):
        """The flag-and-redirect path must actually run end-to-end: no crash,
        no partial computation, a clear message, and a form_type that's
        neither 'itr1' nor 'itr2' so callers can't mistake it for a
        completed computation."""
        form_type, result = run_pipeline(
            [_form16(), _non_resident_status()], session_id="router-nr-1", ay="AY2026-27"
        )
        assert form_type == "unsupported"
        assert "error" in result
        assert "professional" in result["error"]
        assert "itr1_form" not in result
        assert "itr2_form" not in result

    def test_foreign_income_routes_to_itr2_and_computes(self):
        """Foreign income for a Resident filer must actually compute, not
        redirect — end-to-end proof that Schedule FSI runs through the real
        ITR-2 graph rather than only being exercised at the router level."""
        form_type, result = run_pipeline(
            [_form16(), _foreign_income()], session_id="router-fi-2", ay="AY2026-27"
        )
        assert form_type == "itr2"
        assert "itr2_form" in result
        assert result["itr2_form"]["foreign_income"][0]["foreign_income_amount_inr"] == 100000

    def test_domestic_only_still_routes_to_itr1(self):
        form_type, result = run_pipeline([_form16()], session_id="router-itr1-1", ay="AY2026-27")
        assert form_type == "itr1"
        assert "itr1_form" in result

    def test_foreign_income_alone_with_no_form16_still_computes(self):
        """A filer whose entire salary is foreign (no domestic Form 16 at
        all) must still get a computed return, not a hard 'No Form 16
        found' failure — found against a real all-foreign-salary workbook
        uploaded on its own."""
        form_type, result = run_pipeline(
            [_foreign_income()], session_id="router-fi-no-form16", ay="AY2026-27"
        )
        assert form_type == "itr2"
        assert result.get("error") is None
        assert "itr2_form" in result
        assert result["itr2_form"]["foreign_income"][0]["foreign_income_amount_inr"] == 100000
        assert result["itr2_form"]["tax_computation"]["taxable_income"] > 0
