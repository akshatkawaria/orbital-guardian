"""
Decision Agent (build plan §7) -- reference/stub implementation.

run(primary_id, risk_tier, candidates, evaluated) -> Decision dict

Selection rule matches the build plan exactly (§7: "pick the lowest-Δv
candidate among Mission-Agent-approved options that meets the Pc
threshold... keep this rule dead simple and legible"). No approximation
disclaimer needed here -- this genuinely is that simple by design.
"""
from __future__ import annotations


def run(primary_id: str, risk_tier: str, candidates: list[dict], evaluated: list[dict],
        pc_threshold_target: float = 1e-6) -> dict:
    approved_ids = {e["maneuver_id"] for e in evaluated if e["approved"]}
    fuel_by_id = {e["maneuver_id"]: e["fuel_cost_pct"] for e in evaluated}

    eligible = [
        c for c in candidates
        if c["maneuver_id"] in approved_ids and c["predicted_pc"] <= pc_threshold_target
    ]

    if risk_tier in ("GREEN",):
        return {"primary_id": primary_id, "decision": "NO_ACTION", "selected_maneuver": None, "confidence_pct": 98.0}

    if risk_tier in ("YELLOW",) and not eligible:
        return {"primary_id": primary_id, "decision": "MONITOR", "selected_maneuver": None, "confidence_pct": 90.0}

    if not eligible:
        # RED/ORANGE with nothing approved -- still surfaced as MONITOR rather than
        # silently doing nothing, so the dashboard/operator sees the gap explicitly.
        return {"primary_id": primary_id, "decision": "MONITOR", "selected_maneuver": None, "confidence_pct": 60.0}

    best = min(eligible, key=lambda c: c["dv_ms"])
    return {
        "primary_id": primary_id,
        "decision": "MANEUVER_REQUIRED",
        "selected_maneuver": {
            "maneuver_id": best["maneuver_id"],
            "burn_time_utc": best["burn_time_utc"],
            "direction": best["direction"],
            "dv_ms": best["dv_ms"],
            "predicted_miss_km": best["predicted_miss_km"],
            "predicted_pc": best["predicted_pc"],
            "fuel_cost_pct": fuel_by_id.get(best["maneuver_id"], 0.0),
        },
        "confidence_pct": 94.0,
    }
