"""
Data contracts for Agent 9, mirroring Orbital_Guardian_Build_Plan.md §9
(and the upstream agents it reads from: §4 Risk Assessment, §7 Decision
Agent, §6 Mission Constraint Agent).

Keeping these as explicit Pydantic models (rather than raw dicts) means
FastAPI auto-validates every request/response against the contract, and
any other team member's stub/fixture JSON either matches this shape or
fails loudly at the API boundary -- exactly the point of the "JSON
contract, not shared memory" design rule.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# Inputs this agent reads (produced by upstream agents)
# ---------------------------------------------------------------------

class SelectedManeuver(BaseModel):
    maneuver_id: str
    burn_time_utc: str
    direction: str
    dv_ms: float
    predicted_miss_km: float
    predicted_pc: float
    fuel_cost_pct: float


class DecisionAgentOutput(BaseModel):
    """Exact shape produced by Agent 7 (Decision Agent)."""

    primary_id: str
    decision: Literal["NO_ACTION", "MONITOR", "MANEUVER_REQUIRED"]
    selected_maneuver: Optional[SelectedManeuver] = None
    confidence_pct: Optional[float] = None


class ReportContext(BaseModel):
    """
    Optional extra fields joined in from Risk Assessment / Collision
    Detection outputs (Agents 3-4) so the report can name the secondary
    object and describe the pre-maneuver risk. Not required -- Agent 9
    degrades gracefully to a shorter report without it.
    """

    secondary_id: Optional[str] = None
    risk_tier: Optional[str] = None
    time_to_tca_minutes: Optional[float] = None
    miss_distance_km: Optional[float] = None
    rejected_candidates: Optional[List[str]] = None  # e.g. ["M2: altitude_band_exceeded"]


class ExplainRequest(BaseModel):
    decision: DecisionAgentOutput
    context: Optional[ReportContext] = None


# ---------------------------------------------------------------------
# Output this agent produces for report generation (feeds Dashboard)
# ---------------------------------------------------------------------

class ExplainResponse(BaseModel):
    report_text: str
    source: Literal["gemini", "groq", "mock", "deterministic_fallback"]
    numeric_fidelity_ok: bool


# ---------------------------------------------------------------------
# Constraint parsing: input is free text, output feeds Agent 6
# (Mission Constraint Agent). This mirrors the *subset* of Agent 6's
# constraint fields that natural language can plausibly express --
# see Build Plan §6 for the full Mission Constraint Agent schema,
# which also includes catalog-derived fields (other_active_satellites_km)
# that a human would never type and this agent must never invent.
# ---------------------------------------------------------------------

class OperatorCommandRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Free-text operator instruction.")
    primary_id: Optional[str] = Field(
        None, description="Optional satellite this instruction applies to."
    )


class ParsedConstraints(BaseModel):
    protected_ground_regions: List[str] = Field(default_factory=list)
    optimize_for: Literal["min_fuel", "min_miss_distance", "min_dv", "none"] = "none"
    max_fuel_budget_pct_remaining: Optional[float] = None
    altitude_band_km: Optional[List[float]] = None


class OperatorCommandResponse(BaseModel):
    constraints: ParsedConstraints
    source: Literal["gemini", "groq", "mock"]
    raw_text: str


# ---------------------------------------------------------------------
# JSON Schema (plain dict, not Pydantic) handed to Gemini's
# response_schema for controlled generation. Kept in sync with
# ParsedConstraints above -- see tests/test_guardrails.py for the
# consistency check.
# ---------------------------------------------------------------------

CONSTRAINT_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "protected_ground_regions": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Named countries/regions the operator wants ground "
                "coverage preserved over. Empty array if none mentioned."
            ),
        },
        "optimize_for": {
            "type": "string",
            "enum": ["min_fuel", "min_miss_distance", "min_dv", "none"],
            "description": (
                "Only set to a non-'none' value if the operator explicitly "
                "prioritizes fuel, miss distance, or delta-v."
            ),
        },
        "max_fuel_budget_pct_remaining": {
            "type": ["number", "null"],
            "description": "Null unless the operator states a specific fuel budget.",
        },
        "altitude_band_km": {
            "type": ["array", "null"],
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 2,
            "description": "[min_km, max_km]. Null unless an altitude constraint is stated.",
        },
    },
    "required": ["protected_ground_regions", "optimize_for"],
}
