"""
Test — doc-parser/parsers/capital_gains.py (LLM-primary + regex-fallback)
==============================================================================
Mirrors the pattern in test_rag_verification.py: import the module directly
and mock shared.llm_client.complete_with_system rather than hitting a real
provider. Three things worth proving here specifically:

  1. When the LLM path succeeds, its output has the exact shape
     node_fill_form's _merge_docs("capital_gains", "capital_gains_raw")
     expects (same keys the regex path already produces).
  2. holding_period_months is recomputed from acquisition/sale dates in
     Python when both are present, rather than trusting the LLM's own date
     arithmetic — this field alone decides which of four very
     differently-taxed buckets (compute_capital_gains) a transaction lands
     in, so a silent off-by-one here is a real tax-amount bug, not cosmetic.
  3. The regex fallback (pre-existing behavior) still works unchanged when
     the LLM path is unavailable — upgrading capital_gains.py must not
     regress the case with no LLM provider configured.

Run:
    pytest tests/test_capital_gains_parser.py -v
"""
import json
from unittest.mock import patch

from parsers.capital_gains import parse_capital_gains, parse_capital_gains_regex, _months_between


class TestMonthsBetween:
    def test_exact_year_boundary(self):
        assert _months_between("2023-01-15", "2024-01-15") == 12.0

    def test_one_day_short_of_a_full_month(self):
        # 2024-02-14 is one day short of a full 12 months from 2023-02-15 —
        # must round DOWN to 11, not up to 12, since compute_capital_gains
        # uses >= threshold and this transaction is genuinely short-term.
        assert _months_between("2023-02-15", "2024-02-14") == 11.0

    def test_missing_dates_returns_none(self):
        assert _months_between(None, "2024-01-15") is None
        assert _months_between("2023-01-15", None) is None

    def test_malformed_date_returns_none_not_raise(self):
        assert _months_between("not-a-date", "2024-01-15") is None


class TestLLMExtractionPath:
    def test_scrip_wise_transaction_recovers_real_cost_basis(self):
        """The whole point of the LLM upgrade: when the statement has a real
        transaction table, cost_of_acquisition should come from it — not
        collapse to 0 the way the regex summary-only path always does."""
        llm_response = json.dumps([
            {
                "asset_type": "equity_stt",
                "description": "RELIANCE INDUSTRIES LTD",
                "acquisition_date": "2023-01-10",
                "sale_date": "2024-03-20",
                "sale_value": 150000.0,
                "cost_of_acquisition": 100000.0,
            }
        ])
        with patch("shared.llm_client.complete_with_system", return_value=llm_response):
            result = parse_capital_gains("some realistic-length broker statement text " * 5)

        assert result["doc_type"] == "capital_gains"
        assert len(result["capital_gains_raw"]) == 1
        txn = result["capital_gains_raw"][0]
        assert txn["asset_type"] == "equity_stt"
        assert txn["sale_value"] == 150000.0
        assert txn["cost_of_acquisition"] == 100000.0
        # Jan 2023 -> Mar 2024 = 14 months, computed from dates (not left null/0)
        assert txn["holding_period_months"] == 14.0
        assert result["parse_confidence"] == 0.75  # has a real cost basis

    def test_dates_override_llms_own_holding_period_arithmetic(self):
        """If the LLM's holding_period_months disagrees with what the dates
        actually work out to, the date-derived value wins — see module
        docstring on _months_between."""
        llm_response = json.dumps([
            {
                "asset_type": "equity_stt",
                "acquisition_date": "2023-06-01",
                "sale_date": "2023-10-01",   # 4 months -> short-term
                "holding_period_months": 18,  # LLM wrongly says long-term
                "sale_value": 50000.0,
                "cost_of_acquisition": 40000.0,
            }
        ])
        with patch("shared.llm_client.complete_with_system", return_value=llm_response):
            result = parse_capital_gains("some realistic-length broker statement text " * 5)

        assert result["capital_gains_raw"][0]["holding_period_months"] == 4.0

    def test_summary_only_bucket_keeps_zero_cost_basis_and_lower_confidence(self):
        llm_response = json.dumps([
            {
                "asset_type": "equity_stt",
                "description": "Long Term Capital Gains",
                "holding_period_months": 15,
                "sale_value": 80000.0,
                "cost_of_acquisition": 0,
            }
        ])
        with patch("shared.llm_client.complete_with_system", return_value=llm_response):
            result = parse_capital_gains("some realistic-length broker statement text " * 5)

        assert result["capital_gains_raw"][0]["cost_of_acquisition"] == 0.0
        assert result["parse_confidence"] == 0.6  # no cost basis recovered -> same confidence as regex path

    def test_markdown_fenced_response_is_parsed(self):
        llm_response = "```json\n" + json.dumps([
            {"asset_type": "other", "sale_value": 20000.0, "cost_of_acquisition": 15000.0, "holding_period_months": 30}
        ]) + "\n```"
        with patch("shared.llm_client.complete_with_system", return_value=llm_response):
            result = parse_capital_gains("some realistic-length broker statement text " * 5)

        assert len(result["capital_gains_raw"]) == 1
        assert result["capital_gains_raw"][0]["asset_type"] == "other"

    def test_empty_llm_array_falls_back_to_regex(self):
        with patch("shared.llm_client.complete_with_system", return_value="[]"):
            result = parse_capital_gains(
                "Short Term Equity Capital Gains Rs 39,000\nLong Term Equity Capital Gains Rs 80,000"
            )
        # LLM found nothing -> falls back to the regex summary parser, which should
        assert result["capital_gains_raw"]
        assert all(c["cost_of_acquisition"] == 0.0 for c in result["capital_gains_raw"])

    def test_llm_failure_falls_back_to_regex(self):
        with patch("shared.llm_client.complete_with_system", side_effect=RuntimeError("all providers failed")):
            result = parse_capital_gains(
                "Short Term Equity Capital Gains Rs 39,000\nLong Term Equity Capital Gains Rs 80,000"
            )
        assert result["capital_gains_raw"]
        assert result["parse_confidence"] == 0.6


class TestRegexFallbackUnchanged:
    """Pre-existing behavior — must survive the LLM-path addition untouched."""

    def test_four_way_summary_still_parses(self):
        text = (
            "Short Term Equity Capital Gains Rs 39,000\n"
            "Long Term Equity Capital Gains Rs 1,00,000\n"
        )
        result = parse_capital_gains_regex(text)
        assert len(result["capital_gains_raw"]) == 2
        assert result["parse_confidence"] == 0.6
        assert all(c["cost_of_acquisition"] == 0.0 for c in result["capital_gains_raw"])

    def test_no_recognizable_summary_warns(self):
        result = parse_capital_gains_regex("This document contains nothing relevant.")
        assert result["capital_gains_raw"] == []
        assert result["warnings"]

    def test_short_text_skips_llm_and_uses_regex_directly(self):
        """parse_capital_gains() shouldn't even attempt an LLM call for
        near-empty text — cheap, deterministic short-circuit."""
        with patch("shared.llm_client.complete_with_system") as mock_llm:
            result = parse_capital_gains("too short")
            mock_llm.assert_not_called()
        assert result["capital_gains_raw"] == []
