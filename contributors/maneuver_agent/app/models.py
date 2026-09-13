"""
Pydantic request/response models for Agent 5 (Maneuver Agent).

All physical quantities use kilometres, seconds, and km/s internally.
Delta-v *inputs* are given in m/s (matching the CDM-style convention used
by upstream agents) and are converted to km/s at the point of use.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# The six supported burn directions, defined relative to the satellite's
# local RTN (Radial / Transverse / Normal) orbital frame at burn time.
Direction = Literal[
    "prograde",
    "retrograde",
    "radial_out",
    "radial_in",
    "normal",
    "anti_normal",
]

_VALID_DIRECTIONS = {
    "prograde",
    "retrograde",
    "radial_out",
    "radial_in",
    "normal",
    "anti_normal",
}

# Minimum vector magnitudes below which we refuse to build an orbital frame
# (protects against degenerate / non-physical inputs such as r = 0 or a
# purely radial velocity that gives no well-defined orbit normal).
MIN_POSITION_MAG_KM = 1.0
MIN_ANGULAR_MOMENTUM_MAG = 1e-6  # km^2/s


def _check_vec3_finite(name: str, v: List[float]) -> List[float]:
    if len(v) != 3:
        raise ValueError(f"{name} must contain exactly 3 numbers, got {len(v)}")
    for x in v:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise ValueError(f"{name} must contain only numbers")
        if not math.isfinite(x):
            raise ValueError(f"{name} must contain only finite numbers (no NaN/Inf)")
    return [float(x) for x in v]


class StateVector(BaseModel):
    """Position and velocity of an object at a reference epoch."""

    position_km: List[float] = Field(..., description="[x, y, z] in km")
    velocity_km_s: List[float] = Field(..., description="[vx, vy, vz] in km/s")

    @field_validator("position_km")
    @classmethod
    def _validate_position(cls, v: List[float]) -> List[float]:
        v = _check_vec3_finite("position_km", v)
        mag = math.sqrt(sum(c * c for c in v))
        if mag < MIN_POSITION_MAG_KM:
            raise ValueError(
                f"position_km magnitude ({mag:.6g} km) is too small to define an "
                f"orbit; expected a real orbital radius (>= {MIN_POSITION_MAG_KM} km)"
            )
        return v

    @field_validator("velocity_km_s")
    @classmethod
    def _validate_velocity(cls, v: List[float]) -> List[float]:
        return _check_vec3_finite("velocity_km_s", v)

    @model_validator(mode="after")
    def _validate_angular_momentum(self) -> "StateVector":
        r = self.position_km
        v = self.velocity_km_s
        h = [
            r[1] * v[2] - r[2] * v[1],
            r[2] * v[0] - r[0] * v[2],
            r[0] * v[1] - r[1] * v[0],
        ]
        h_mag = math.sqrt(sum(c * c for c in h))
        if h_mag < MIN_ANGULAR_MOMENTUM_MAG:
            raise ValueError(
                "position and velocity vectors are (near-)parallel; the "
                "specific angular momentum r x v is too small to define an "
                "orbital plane / local orbital frame"
            )
        return self


class ManeuverRequest(BaseModel):
    """Input contract: a predicted close-approach event from Agents 3/4."""

    event_id: str
    satellite_id: str
    debris_id: str
    reference_time: datetime = Field(
        ..., description="UTC epoch at which satellite_state/debris_state are valid"
    )
    time_to_closest_approach_s: float = Field(
        ..., gt=0, description="Seconds from reference_time to predicted TCA"
    )
    burn_lead_time_s: float = Field(
        ..., gt=0, description="Seconds from reference_time until the burn is executed"
    )
    satellite_state: StateVector
    debris_state: StateVector
    baseline_miss_distance_km: float = Field(..., ge=0)
    target_miss_distance_km: float = Field(..., gt=0)
    allowed_directions: List[Direction] = Field(..., min_length=1)
    delta_v_options_m_s: List[float] = Field(..., min_length=1)
    max_delta_v_m_s: float = Field(..., gt=0)

    @field_validator("allowed_directions")
    @classmethod
    def _validate_directions(cls, v: List[str]) -> List[str]:
        bad = [d for d in v if d not in _VALID_DIRECTIONS]
        if bad:
            raise ValueError(
                f"unsupported maneuver direction(s): {bad}; "
                f"supported directions are {sorted(_VALID_DIRECTIONS)}"
            )
        if len(set(v)) != len(v):
            raise ValueError("allowed_directions contains duplicates")
        return v

    @field_validator("delta_v_options_m_s")
    @classmethod
    def _validate_dv_options(cls, v: List[float]) -> List[float]:
        for dv in v:
            if not math.isfinite(dv) or dv <= 0:
                raise ValueError(
                    f"delta_v_options_m_s values must be positive and finite, got {dv}"
                )
        return v

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "ManeuverRequest":
        if self.burn_lead_time_s >= self.time_to_closest_approach_s:
            raise ValueError(
                "burn_lead_time_s must be strictly less than "
                "time_to_closest_approach_s (the burn must occur before the "
                "predicted encounter)"
            )
        over_max = [dv for dv in self.delta_v_options_m_s if dv > self.max_delta_v_m_s]
        if over_max:
            raise ValueError(
                f"delta_v_options_m_s values {over_max} exceed max_delta_v_m_s "
                f"({self.max_delta_v_m_s})"
            )
        return self


class ManeuverCandidate(BaseModel):
    candidate_id: str
    direction: Direction
    delta_v_m_s: float
    burn_time: datetime
    projected_miss_distance_km: float
    improvement_km: float
    meets_target: bool
    fuel_cost_score: float
    rank: int
    explanation: str


class ManeuverResponse(BaseModel):
    event_id: str
    model: str = "two_body_demo"
    baseline_miss_distance_km: float
    target_miss_distance_km: float
    candidates: List[ManeuverCandidate]
    recommended_candidate_id: Optional[str]
    warnings: List[str]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "maneuver-agent"
