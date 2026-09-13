"""
Pydantic models for the Mission Constraint Agent's input/output contract.
These mirror Orbital_Guardian_Build_Plan.md §6 field-for-field — this file
*is* the contract, made enforceable. Any other agent (Maneuver, Decision,
Dashboard) can import these models to validate what they send/receive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["along_track", "radial", "cross_track"]


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------

class ManeuverCandidate(BaseModel):
    maneuver_id: str
    burn_time_utc: str
    direction: Direction
    dv_ms: float
    predicted_miss_km: float
    predicted_pc: float
    fuel_cost_pct: float | None = None  # some Maneuver Agent variants may omit this pre-check


class OtherActiveSatellite(BaseModel):
    object_id: str
    r_km: tuple[float, float, float]


class MissionConstraints(BaseModel):
    altitude_band_km: tuple[float, float]
    protected_ground_regions: list[str] = Field(default_factory=list)
    max_fuel_budget_pct_remaining: float
    other_active_satellites_km: list[OtherActiveSatellite] = Field(default_factory=list)


class MissionConstraintRequest(BaseModel):
    primary_id: str
    candidates: list[ManeuverCandidate]
    constraints: MissionConstraints


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

class EvaluatedCandidate(BaseModel):
    maneuver_id: str
    approved: bool
    fuel_cost_pct: float
    violations: list[str] = Field(default_factory=list)


class MissionConstraintResponse(BaseModel):
    primary_id: str
    evaluated: list[EvaluatedCandidate]
