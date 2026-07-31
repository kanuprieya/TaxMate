"""
Capital Gains Statement Parser
=================================
ITR-2 only — parses broker/RTA capital gains statements (the summary most
brokers issue for tax filing, e.g. Zerodha/Groww/ICICI Direct/CAMS "Capital
Gains Statement"). These typically print a 4-way summary rather than a raw
transaction ledger: short/long term x equity/other. This parser looks for
that summary and synthesizes one pseudo-transaction per non-zero bucket,
which shared.tax_engine's compute_capital_gains primitive then classifies
using the same holding-period rule it applies to individually-listed
transactions — asset_type + a holding period safely on the correct side of
the equity(12mo)/other(24mo) threshold is enough for it to land in the right
bucket, even though we only ever learned the net gain, not the underlying
buy/sell values (so cost_of_acquisition is left at 0 and the full gain is
carried as sale_value — a documented simplification, not a real
buy/sell reconstruction).

This is a first-cut heuristic parser against one common statement layout,
same spirit as doc-parser/parsers/ais.py's SFT-pattern matching — not a
parser for every broker's PDF format.
"""

import re


def parse_indian_amount(raw: str) -> float:
    if not raw:
        return 0.0
    clean = re.sub(r"[^\d.]", "", str(raw).replace(",", ""))
    try:
        return float(clean)
    except ValueError:
        return 0.0


# The currency marker is mandatory (not optional) — without it, a lazy
# '.*?' will happily latch onto the nearest unrelated number (a year, a page
# number) instead of the amount. See property.py for the concrete failure
# this was found against.
CURRENCY = r"(?:Rs\.?|INR|₹)"

CAPITAL_GAINS_PATTERNS = {
    # (asset_type, holding_period_months placed safely on one side of the
    # threshold used by compute_capital_gains's default params)
    "stcg_111a":      (rf"Short[\s-]*Term.*?Equity.*?{CURRENCY}\s*([\d,]+\.?\d*)", "equity_stt", 6),
    "ltcg_112a":       (rf"Long[\s-]*Term.*?Equity.*?{CURRENCY}\s*([\d,]+\.?\d*)", "equity_stt", 13),
    "stcg_other":      (rf"Short[\s-]*Term.*?(?:Debt|Other|Non-?Equity).*?{CURRENCY}\s*([\d,]+\.?\d*)", "other", 6),
    "ltcg_112_other":  (rf"Long[\s-]*Term.*?(?:Debt|Other|Non-?Equity).*?{CURRENCY}\s*([\d,]+\.?\d*)", "other", 30),
}


def parse_capital_gains(text: str) -> dict:
    result = {
        "doc_type": "capital_gains",
        "capital_gains_raw": [],
        "parse_confidence": 0.0,
        "warnings": [],
    }

    for bucket, (pattern, asset_type, holding_months) in CAPITAL_GAINS_PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            gain = parse_indian_amount(match.group(1))
            if gain > 0:
                result["capital_gains_raw"].append({
                    "asset_type": asset_type,
                    "description": bucket,
                    "holding_period_months": holding_months,
                    "sale_value": gain,
                    "cost_of_acquisition": 0.0,
                })

    if result["capital_gains_raw"]:
        result["parse_confidence"] = 0.6
    else:
        result["warnings"].append(
            "Could not find a recognizable short/long-term equity/other capital gains "
            "summary in this document. Enter capital gains manually."
        )

    return result
