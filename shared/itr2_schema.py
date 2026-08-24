"""
ITR-2 Form Schema — AY 2026-27
=================================
Pydantic models for the ITR-2-only sections: multiple house properties,
Schedule CG (capital gains), and Schedule FSI/FA (foreign income/assets —
computed for Resident filers via shared.tax_engine.primitives.
aggregate_foreign_income/apply_foreign_tax_credit; Non-Resident/RNOR
filings are still flagged out of scope by graph/router.py's
is_out_of_scope() before an ITR2Form is ever built).

Everything reusable from ITR-1 (personal info, salary, deductions, TDS, tax
computation, confidence/validation shapes) is imported directly from
shared/itr1_schema.py rather than duplicated — those are generic tax-filing
concepts, not ITR-1-specific business rules. This file is purely additive:
it does not modify shared/itr1_schema.py.

Used by: agent-orchestrator/graph/itr2_graph.py (node_fill_form).
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field

from shared.itr1_schema import (
    TaxRegime,
    FilingStatus,
    ResidentialStatus,
    PersonalInfo,
    SalaryIncome,
    OtherSourcesIncome,
    Deductions,
    TDSEntry,
    TaxComputation,
    FieldConfidence,
    ValidationFlag,
)

__all__ = [
    "TaxRegime", "FilingStatus", "ResidentialStatus", "PersonalInfo",
    "SalaryIncome", "OtherSourcesIncome", "Deductions", "TDSEntry",
    "TaxComputation", "FieldConfidence", "ValidationFlag",
    "HousePropertyEntry", "CapitalGainsEntry",
    "ForeignIncomeEntry", "ForeignAssetEntry", "ITR2Form",
]

# ── Section: Schedule HP (multiple house properties) ─────────────────────────

class HousePropertyEntry(BaseModel):
    """One property in Schedule HP. ITR-1's HousePropertyIncome models a
    single implicit property; ITR-2 allows more than one, so this is a list
    item rather than a singleton section."""

    address:                  Optional[str] = None
    property_type:            str   = "self_occupied"   # self_occupied / let_out
    annual_value:              float = 0.0
    municipal_tax_paid:        float = 0.0
    interest_on_loan_24b:      float = 0.0
    co_owner_share_pct:        float = 100.0

    # Filled by shared.tax_engine's aggregate_house_properties primitive
    net_annual_value:          Optional[float] = None
    income:                    Optional[float] = None


# ── Section: Schedule CG (capital gains) ──────────────────────────────────────

class CapitalGainsEntry(BaseModel):
    """One transaction in Schedule CG. asset_type distinguishes STT-paid
    equity/equity mutual funds (Sec 111A/112A special rates) from every other
    capital asset (Sec 112)."""

    asset_type:               str = "other"   # "equity_stt" | "other"
    description:              Optional[str] = None
    acquisition_date:         Optional[str] = None   # YYYY-MM-DD
    sale_date:                Optional[str] = None   # YYYY-MM-DD
    holding_period_months:    float = 0.0
    sale_value:                float = 0.0
    cost_of_acquisition:       float = 0.0
    improvement_cost:          float = 0.0
    exemption_claimed:         float = 0.0   # Sec 54/54EC/54F etc.
    exemption_section:         Optional[str] = None

    # Filled by shared.tax_engine's compute_capital_gains primitive
    is_long_term:              Optional[bool] = None
    gain:                      Optional[float] = None


class CapitalGainsSummary(BaseModel):
    """Aggregated Schedule CG output — mirrors the 'capital_gains' bucket
    dict the tax engine produces."""

    stcg_111a:     float = 0.0
    ltcg_112a:     float = 0.0
    ltcg_112_other: float = 0.0
    # Non-equity short-term gains: taxed at slab rate (not a special rate),
    # folded into other_source_income/taxable_income — correctly 87A-eligible,
    # unlike the three buckets above. Kept here purely for display, so a
    # filer can see where this slice of their slab tax comes from instead of
    # it being an invisible addition on top of salary.
    stcg_slab:     float = 0.0
    capital_gains_tax: float = 0.0


# ── Section: Schedule FSI / FA (foreign income / foreign assets) ─────────────

class ForeignIncomeEntry(BaseModel):
    """One foreign-income entry in Schedule FSI. income_type only
    distinguishes "salary" (folds into salary income, one shared standard
    deduction) from "other" (interest/dividend/rental/etc, folds into other
    sources) — this engine doesn't compute per-country/per-head DTAA
    credits separately, see apply_foreign_tax_credit's docstring for the
    blended-average-rate approximation this uses instead."""

    country:                   Optional[str] = None
    income_type:                str = "other"   # "salary" | "other"
    foreign_income_amount_inr:  float = 0.0
    foreign_tax_paid_inr:       float = 0.0


class ForeignAssetEntry(BaseModel):
    """One foreign asset disclosure in Schedule FA. Disclosure only — never
    affects the tax computation itself, only appears here for the filer's
    own record/reference."""

    country:            Optional[str] = None
    asset_type:          str = "bank_account"   # "bank_account" | "equity" | "property" | "other"
    peak_value_inr:       float = 0.0
    closing_value_inr:    float = 0.0


# ── Master ITR-2 Form ─────────────────────────────────────────────────────────

class ITR2Form(BaseModel):
    """Complete ITR-2 form. Structurally the same shape as ITR1Form for the
    sections they share, plus Schedule HP as a list, Schedule CG, and
    Schedule FSI/FA."""

    personal_info:        PersonalInfo               = Field(default_factory=PersonalInfo)
    salary_income:        SalaryIncome               = Field(default_factory=SalaryIncome)
    house_properties:     list[HousePropertyEntry]   = Field(default_factory=list)
    house_property_loss_carried_forward: float       = 0.0
    capital_gains:        list[CapitalGainsEntry]    = Field(default_factory=list)
    capital_gains_summary: CapitalGainsSummary        = Field(default_factory=CapitalGainsSummary)
    foreign_income:        list[ForeignIncomeEntry]   = Field(default_factory=list)
    foreign_assets:         list[ForeignAssetEntry]    = Field(default_factory=list)
    foreign_tax_credit:     float                      = 0.0
    # True only if a residential_status document was uploaded and confirmed
    # "resident" (NR/RNOR never reach this graph — graph/router.py redirects
    # those before ITR2Form is ever built). False means residency was never
    # actually checked and personal_info.residential_status is still just
    # the schema default — this computation is only valid for a Resident
    # and Ordinarily Resident filer, so an unconfirmed status with real
    # foreign income present is a load-bearing assumption, not a formality.
    residential_status_confirmed: bool = False
    other_sources:        OtherSourcesIncome         = Field(default_factory=OtherSourcesIncome)
    deductions:            Deductions                 = Field(default_factory=Deductions)
    tds_details:           list[TDSEntry]             = Field(default_factory=list)
    tax_computation:       TaxComputation             = Field(default_factory=TaxComputation)

    confidence_scores:    dict[str, FieldConfidence] = Field(default_factory=dict)
    validation_flags:     list[ValidationFlag]        = Field(default_factory=list)
    regime_recommendation: Optional[str]              = None
    regime_tax_old:       float                       = 0.0
    regime_tax_new:       float                       = 0.0
    audit_trail:          list[dict]                  = Field(default_factory=list)

    session_id:           Optional[str] = None
    created_at:           Optional[str] = None
    ay:                   str           = "AY2026-27"
