"""
risk_agent.py - Risk Assessment Agent (Phase 4).

Input: one conjunction record from the Collision Detection Agent (Phase 3),
plus object physical parameters.
Output: Pc + categorical risk tier, feeding Maneuver (5), Decision (7), and
the Dashboard (10).
"""
from datetime import datetime, timezone
from .chan_pc import chan_pc

# Risk tier thresholds matching common operational practice
# (see published CAM-optimization studies for these Pc bands).
_TIER_THRESHOLDS = [
    (1e-4, "RED"),
    (1e-5, "ORANGE"),
    (1e-6, "YELLOW"),
]


def classify_risk_tier(pc: float) -> str:
    for threshold, tier in _TIER_THRESHOLDS:
        if pc >= threshold:
            return tier
    return "GREEN"


def _minutes_until(tca_utc: str) -> float:
    tca_dt = datetime.fromisoformat(tca_utc.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return (tca_dt - now).total_seconds() / 60.0


def assess_risk(conjunction: dict) -> dict:
    """
    conjunction: matches Phase 4's input contract (see build plan sec 4 /
    the implementation guide).
    Returns: matches Phase 4's output contract exactly.
    """
    result = chan_pc(
        relative_position_km=conjunction["relative_position_km"],
        relative_velocity_kms=conjunction["relative_velocity_vec_kms"],
        combined_hard_body_radius_m=conjunction["combined_hard_body_radius_m"],
        cov_primary_km2=conjunction["position_covariance_primary_km2"],
        cov_secondary_km2=conjunction["position_covariance_secondary_km2"],
    )

    pc = result["pc"]

    return {
        "primary_id": conjunction["primary_id"],
        "secondary_id": conjunction["secondary_id"],
        "pc": pc,
        "pc_method": "Gaussian encounter-plane integration (assumed demo covariance)",
        "risk_tier": classify_risk_tier(pc),
        "time_to_tca_minutes": round(_minutes_until(conjunction["tca_utc"]), 1),
        "miss_distance_km": round(result["miss_distance_km"], 4),
    }
