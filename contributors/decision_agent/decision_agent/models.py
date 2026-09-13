"""
Data models for the Decision Agent's JSON contracts.

Plain stdlib dataclasses on purpose — no pydantic/FastAPI dependency here so
this package stays importable in isolation (unit tests, a notebook, another
agent's fixture generator) without pulling in the web framework. The
Dashboard/API layer is free to wrap these in pydantic models for request
validation; `from_dict` / `to_dict` are the seams for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


VALID_RISK_TIERS = ("GREEN", "YELLOW", "ORANGE", "RED")

VALID_DECISIONS = (
    "NO_ACTION",
    "MONITOR",
    "MANEUVER_REQUIRED",
    "ESCALATE_NO_VIABLE_OPTION",
)


class SchemaError(ValueError):
    """Raised when input JSON doesn't match the expected Decision Agent contract."""


@dataclass
class EvaluatedCandidate:
    """One maneuver candidate as annotated by the Mission Constraint Agent."""

    maneuver_id: str
    approved: bool
    dv_ms: float
    predicted_pc: float
    predicted_miss_km: Optional[float] = None
    fuel_cost_pct: Optional[float] = None
    burn_time_utc: Optional[str] = None
    direction: Optional[str] = None
    violations: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvaluatedCandidate":
        required = ("maneuver_id", "approved", "dv_ms", "predicted_pc")
        missing = [k for k in required if k not in d]
        if missing:
            raise SchemaError(f"candidate missing required field(s): {missing} in {d}")
        return cls(
            maneuver_id=d["maneuver_id"],
            approved=bool(d["approved"]),
            dv_ms=float(d["dv_ms"]),
            predicted_pc=float(d["predicted_pc"]),
            predicted_miss_km=d.get("predicted_miss_km"),
            fuel_cost_pct=d.get("fuel_cost_pct"),
            burn_time_utc=d.get("burn_time_utc"),
            direction=d.get("direction"),
            violations=list(d.get("violations", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionInput:
    """Merged input contract — see README §Input schema for rationale."""

    primary_id: str
    risk_tier: str
    pc: float
    evaluated_candidates: List[EvaluatedCandidate]
    secondary_id: Optional[str] = None
    time_to_tca_minutes: Optional[float] = None
    pc_threshold_target: float = 1e-6
    conjunction_id: Optional[str] = None

    def __post_init__(self):
        if self.risk_tier not in VALID_RISK_TIERS:
            raise SchemaError(
                f"risk_tier must be one of {VALID_RISK_TIERS}, got {self.risk_tier!r}"
            )
        if not self.conjunction_id and self.secondary_id:
            self.conjunction_id = f"{self.primary_id}_vs_{self.secondary_id}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionInput":
        required = ("primary_id", "risk_tier", "pc")
        missing = [k for k in required if k not in d]
        if missing:
            raise SchemaError(f"input missing required field(s): {missing}")

        raw_candidates = d.get("evaluated_candidates")
        if raw_candidates is None:
            # Back-compat: accept the build plan's pre-filtered
            # `approved_candidates` shape and synthesize `approved: true`.
            raw_candidates = [
                {**c, "approved": True} for c in d.get("approved_candidates", [])
            ]
        candidates = [EvaluatedCandidate.from_dict(c) for c in raw_candidates]

        return cls(
            primary_id=d["primary_id"],
            secondary_id=d.get("secondary_id"),
            risk_tier=d["risk_tier"],
            pc=float(d["pc"]),
            time_to_tca_minutes=d.get("time_to_tca_minutes"),
            evaluated_candidates=candidates,
            pc_threshold_target=float(d.get("pc_threshold_target", 1e-6)),
            conjunction_id=d.get("conjunction_id"),
        )


@dataclass
class DecisionOutput:
    """Output contract — feeds Negotiation Agent / Dashboard / Explanation Layer."""

    primary_id: str
    decision: str
    decision_reason: str
    decided_at_utc: str
    secondary_id: Optional[str] = None
    conjunction_id: Optional[str] = None
    selected_maneuver: Optional[Dict[str, Any]] = None
    rejected_candidates: List[Dict[str, Any]] = field(default_factory=list)
    confidence_pct: Optional[int] = None

    def __post_init__(self):
        if self.decision not in VALID_DECISIONS:
            raise SchemaError(
                f"decision must be one of {VALID_DECISIONS}, got {self.decision!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
