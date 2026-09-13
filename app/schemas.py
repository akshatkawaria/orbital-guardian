"""
Pydantic models mirroring the JSON contracts in Orbital_Guardian_Build_Plan.md,
one section per agent. These are the single source of truth for validation at
every agent-call boundary (see pipeline.py) and for the FastAPI request/response
bodies in main.py.

Kept deliberately close to the exact field names in the build plan so a real
agent implementation can be dropped in with zero renaming.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. Tracking Agent
# ---------------------------------------------------------------------------
class MeanElements(BaseModel):
    inclination_deg: float
    raan_deg: float
    eccentricity: float
    arg_perigee_deg: float
    mean_anomaly_deg: float
    mean_motion_rev_day: float
    bstar: float = 0.0


class TrackedObject(BaseModel):
    object_id: str
    object_type: Literal["PAYLOAD", "ROCKET_BODY", "DEBRIS"]
    epoch_utc: str
    mean_elements: MeanElements
    last_updated: str


class CatalogRefreshRequest(BaseModel):
    mode: Literal["synthetic", "celestrak"] = "synthetic"
    object_count: int = 50
    group: Optional[str] = None
    guaranteed_conjunctions: int = 3


# ---------------------------------------------------------------------------
# 2. Prediction Agent
# ---------------------------------------------------------------------------
class EphemerisPoint(BaseModel):
    t_utc: str
    r_km: list[float]
    v_kms: list[float]


class EphemerisRecord(BaseModel):
    object_id: str
    frame: str = "ECI_J2000"
    ephemeris: list[EphemerisPoint]


class PropagateRequest(BaseModel):
    object_ids: Optional[list[str]] = None   # None = whole catalog
    window_hours: float = 24.0
    step_seconds: int = 60


# ---------------------------------------------------------------------------
# 3. Collision Detection Agent
# ---------------------------------------------------------------------------
class Conjunction(BaseModel):
    primary_id: str
    secondary_id: str
    tca_utc: str
    miss_distance_km: float
    relative_velocity_kms: float
    relative_position_km: list[float]
    relative_velocity_vec_kms: list[float]


class ConjunctionsResponse(BaseModel):
    screening_window: dict
    pairs_screened: int
    conjunctions: list[Conjunction]


# ---------------------------------------------------------------------------
# 4. Risk Assessment Agent
# ---------------------------------------------------------------------------
class RiskRecord(BaseModel):
    primary_id: str
    secondary_id: str
    pc: float
    pc_method: str
    risk_tier: Literal["GREEN", "YELLOW", "ORANGE", "RED"]
    time_to_tca_minutes: float
    miss_distance_km: float


# ---------------------------------------------------------------------------
# 5. Maneuver Planning Agent
# ---------------------------------------------------------------------------
class ManeuverCandidate(BaseModel):
    maneuver_id: str
    burn_time_utc: str
    direction: Literal["along_track", "radial", "cross_track"]
    dv_ms: float
    predicted_miss_km: float
    predicted_pc: float


class ManeuverCandidatesResponse(BaseModel):
    primary_id: str
    candidates: list[ManeuverCandidate]


# ---------------------------------------------------------------------------
# 6. Mission Constraint Agent
# ---------------------------------------------------------------------------
class MissionConstraints(BaseModel):
    altitude_band_km: Optional[list[float]] = None
    protected_ground_regions: list[str] = Field(default_factory=list)
    max_fuel_budget_pct_remaining: Optional[float] = None
    optimize_for: Optional[str] = None


class EvaluatedCandidate(BaseModel):
    maneuver_id: str
    approved: bool
    fuel_cost_pct: float
    violations: list[str] = Field(default_factory=list)


class EvaluatedCandidatesResponse(BaseModel):
    primary_id: str
    evaluated: list[EvaluatedCandidate]


# ---------------------------------------------------------------------------
# 7. Decision Agent
# ---------------------------------------------------------------------------
class SelectedManeuver(BaseModel):
    maneuver_id: str
    burn_time_utc: str
    direction: str
    dv_ms: float
    predicted_miss_km: float
    predicted_pc: float
    fuel_cost_pct: float


class Decision(BaseModel):
    primary_id: str
    decision: Literal["NO_ACTION", "MONITOR", "MANEUVER_REQUIRED", "ESCALATE_NO_VIABLE_OPTION"]
    selected_maneuver: Optional[SelectedManeuver] = None
    confidence_pct: Optional[float] = None


# ---------------------------------------------------------------------------
# 8. Negotiation Agent
# ---------------------------------------------------------------------------
class NegotiationProposal(BaseModel):
    object_id: str
    min_dv_ms_to_clear: Optional[float] = Field(default=None,ge=0,allow_inf_nan=False)
    fuel_remaining_pct: float = Field(ge=0,le=100,allow_inf_nan=False)


class NegotiationRequest(BaseModel):
    conjunction_id: str
    proposals: list[NegotiationProposal] = Field(min_length=2,max_length=2)


class NegotiationResult(BaseModel):
    conjunction_id: str
    mover: str
    rationale: str
    dv_ms: float


# ---------------------------------------------------------------------------
# 9. Explanation / LLM Layer
# ---------------------------------------------------------------------------
class OperatorCommandRequest(BaseModel):
    text: str


class ReportText(BaseModel):
    report_text: str


# ---------------------------------------------------------------------------
# 10. Dashboard / Orchestration layer's own contracts
# ---------------------------------------------------------------------------
class AgentLogEvent(BaseModel):
    agent: str
    status: Literal["started", "completed", "failed", "skipped"]
    detail: str
    ts: str


class ManeuverRequestBody(BaseModel):
    risk_tier: Optional[str] = None
    pc_threshold_target: float = 1e-6
    constraints: MissionConstraints = Field(default_factory=MissionConstraints)


class SimulateStartRequest(BaseModel):
    object_count: int = Field(default=20,ge=2,le=60)
    window_hours: float = Field(default=2,ge=1,le=6)
    guaranteed_conjunctions: int = Field(default=1,ge=0,le=3)
