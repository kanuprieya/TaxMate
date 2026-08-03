"""
ITR-2 Form Schema — AY 2026-27
=================================
Pydantic models for the ITR-2-only sections: multiple house properties and
Schedule CG (capital gains). Schedule FA/FSI (foreign assets & income) is
explicitly out of scope — graph/router.py's is_out_of_scope() flags and
redirects those filings before an ITR2Form is ever built, so there's no
foreign-income schema here to leave unused.

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
    "HousePropertyEntry", "CapitalGainsEntry", "ITR2Form",
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
    capital_gains_tax: float = 0.0


# ── Master ITR-2 Form ─────────────────────────────────────────────────────────

class ITR2Form(BaseModel):
    """Complete ITR-2 form (excluding Schedule FA/FSI — out of scope; see
    module docstring). Structurally the same shape as ITR1Form for the
    sections they share, plus Schedule HP as a list and Schedule CG."""

    personal_info:        PersonalInfo               = Field(default_factory=PersonalInfo)
    salary_income:        SalaryIncome               = Field(default_factory=SalaryIncome)
    house_properties:     list[HousePropertyEntry]   = Field(default_factory=list)
    house_property_loss_carried_forward: float       = 0.0
    capital_gains:        list[CapitalGainsEntry]    = Field(default_factory=list)
    capital_gains_summary: CapitalGainsSummary        = Field(default_factory=CapitalGainsSummary)
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
