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
        "foreign_income": [{"country": country, "foreign_income_amount": 100000, "foreign_tax_paid": 10000}],
        "foreign_assets": [],
    }}


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


class TestIsOutOfScope:

    def test_no_foreign_docs_is_in_scope(self):
        assert is_out_of_scope([_form16(), _properties(3)]) is None

    def test_foreign_income_doc_is_out_of_scope(self):
        message = is_out_of_scope([_form16(), _foreign_income("Germany")])
        assert message is not None
        assert "professional" in message
        assert "Germany" in message


class TestRunPipelineEndToEnd:

    def test_foreign_income_short_circuits_before_either_graph(self):
        """The flag-and-redirect path must actually run end-to-end: no crash,
        no partial computation, a clear message, and a form_type that's
        neither 'itr1' nor 'itr2' so callers can't mistake it for a
        completed computation."""
        form_type, result = run_pipeline(
            [_form16(), _foreign_income()], session_id="router-fi-1", ay="AY2026-27"
        )
        assert form_type == "unsupported"
        assert "error" in result
        assert "professional" in result["error"]
        assert "itr1_form" not in result
        assert "itr2_form" not in result

    def test_domestic_only_still_routes_to_itr1(self):
        form_type, result = run_pipeline([_form16()], session_id="router-itr1-1", ay="AY2026-27")
        assert form_type == "itr1"
        assert "itr1_form" in result
