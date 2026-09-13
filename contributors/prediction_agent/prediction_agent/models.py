"""
Data contracts for the Prediction Agent.

These mirror the JSON schemas in Orbital_Guardian_Build_Plan.md §2 exactly,
field-for-field, so that the Tracking Agent -> Prediction Agent -> Collision
Detection Agent handoff never needs translation code in either neighbor.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Input: what the Tracking Agent hands us, plus the requested time window
# --------------------------------------------------------------------------- #

class MeanElements(BaseModel):
    inclination_deg: float
    raan_deg: float
    eccentricity: float
    arg_perigee_deg: float
    mean_anomaly_deg: float
    mean_motion_rev_day: float
    bstar: float


class PredictionRequest(BaseModel):
    """One Tracking Agent record + a requested propagation window."""

    object_id: str
    mean_elements: MeanElements
    propagate_from_utc: str
    propagate_to_utc: str
    step_seconds: int = Field(gt=0)

    # Optional passthrough fields some Tracking Agent records may include;
    # not required by propagation but harmless to accept so this model
    # tolerates the full Tracking Agent output being handed in directly.
    object_type: Optional[Literal["PAYLOAD", "ROCKET_BODY", "DEBRIS"]] = None
    epoch_utc: Optional[str] = None
    last_updated: Optional[str] = None

    @field_validator("propagate_to_utc")
    @classmethod
    def _window_is_ordered(cls, v: str, info) -> str:
        frm = info.data.get("propagate_from_utc")
        if frm is not None and v <= frm:
            raise ValueError("propagate_to_utc must be after propagate_from_utc")
        return v


class BatchPredictionRequest(BaseModel):
    """Multiple objects propagated over the same shared window (catalog-scale use)."""

    propagate_from_utc: str
    propagate_to_utc: str
    step_seconds: int = Field(gt=0)
    objects: list[dict] = Field(
        description="List of {object_id, mean_elements} records sharing the window above."
    )


# --------------------------------------------------------------------------- #
# Output: what the Collision Detection Agent (and dashboard/globe) consume
# --------------------------------------------------------------------------- #

class EphemerisPoint(BaseModel):
    t_utc: str
    r_km: tuple[float, float, float]
    v_kms: tuple[float, float, float]


class PredictionOutput(BaseModel):
    object_id: str
    frame: Literal["ECI_J2000"] = "ECI_J2000"
    ephemeris: list[EphemerisPoint]

    # Not part of the original contract's happy-path shape, but additive
    # (ignorable by any consumer that only reads object_id/frame/ephemeris)
    # and cheap insurance against silently propagating a decayed object.
    warnings: list[str] = Field(default_factory=list)


class BatchPredictionOutput(BaseModel):
    screening_window: dict
    predictions: list[PredictionOutput]
