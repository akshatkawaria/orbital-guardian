"""
Maneuver Planning Agent (build plan §5) -- reference/stub implementation.

run(maneuver_input) -> {"primary_id": ..., "candidates": [...]}

HONEST LIMITATION -- read before trusting these numbers:
A rigorous implementation propagates the Clohessy-Wiltshire equations from a
burn state to TCA and re-projects the resulting relative-position shift into
the SAME encounter-plane basis the Risk Assessment Agent used for Pc (build
plan §5's documented approach). Doing that correctly requires reconciling
two different local frames (the primary's LVLH frame that CW is defined in,
vs. the encounter plane perpendicular to relative velocity that Pc is
computed in) -- a real, non-trivial piece of orbital mechanics that is
exactly the actual research work assigned to whoever builds this agent for
real, per the build plan.

This stub instead uses two much simpler, explicitly-labeled approximations
straight out of the build plan's own reference material (§5's citations):
  - along_track: the drift-rate rule of thumb from arXiv:1002.2277,
    "1 cm/s of along-track Δv ≈ 1 km/day of secular in-track drift."
  - radial / cross_track: the oscillatory CW solutions x(t), z(t) for an
    impulsive Δv with zero initial position offset.
The resulting position shift is combined with the original miss distance in
quadrature (new_miss = sqrt(d0^2 + delta^2)) and Pc is rescaled by an
inverse-square falloff (Pc ~ (d0/d1)^2 * Pc0) -- a standard order-of-
magnitude proxy for how a 2D Gaussian tail shrinks as mean separation grows,
used here because re-deriving the exact new Pc would require the same
frame-reconciliation problem noted above.

Net effect: candidate Δv magnitudes and the *direction* of the trend (more
Δv -> lower Pc, more Δv -> larger miss distance) are realistic and useful
for driving the dashboard/demo end-to-end. Treat the exact numeric values as
illustrative, not flight-quality, until a real CW-to-encounter-plane
implementation replaces this module.
"""
from __future__ import annotations
import math

from app.config import MAX_DV_SEARCH_MS, DV_SEARCH_STEPS


def _shift_km(direction: str, dv_ms: float, lead_time_s: float, n_rad_s: float) -> float:
    dv_kms = dv_ms / 1000.0
    if direction == "along_track":
        # rule of thumb: 1 cm/s along-track ≈ 1 km/day secular drift => 1 m/s ≈ 100 km/day
        drift_km_per_day = dv_ms * 100.0
        lead_time_days = lead_time_s / 86400.0
        return drift_km_per_day * lead_time_days
    else:  # radial or cross_track -- CW oscillatory response, zero initial offset
        n = max(n_rad_s, 1e-6)
        return abs((dv_kms / n) * math.sin(n * lead_time_s))


def _pc_after_shift(pc0: float, miss0_km: float, shift_km: float) -> tuple[float, float]:
    new_miss = math.sqrt(miss0_km ** 2 + shift_km ** 2)
    if new_miss <= 1e-9:
        return pc0, miss0_km
    scale = (miss0_km / new_miss) ** 2 if miss0_km > 0 else 1.0
    return max(pc0 * scale, 1e-15), new_miss


def _search_direction(direction: str, pc0: float, miss0_km: float, lead_time_s: float,
                       n_rad_s: float, pc_threshold_target: float) -> tuple[float, float, float]:
    """Linear search over Δv magnitude for the smallest value that clears the Pc threshold."""
    best = None
    for step in range(1, DV_SEARCH_STEPS + 1):
        dv = MAX_DV_SEARCH_MS * step / DV_SEARCH_STEPS
        shift = _shift_km(direction, dv, lead_time_s, n_rad_s)
        pc, miss = _pc_after_shift(pc0, miss0_km, shift)
        if pc <= pc_threshold_target:
            best = (dv, miss, pc)
            break
    if best is None:
        # Didn't clear the bar within the search budget -- return the best (largest) tried.
        dv = MAX_DV_SEARCH_MS
        shift = _shift_km(direction, dv, lead_time_s, n_rad_s)
        pc, miss = _pc_after_shift(pc0, miss0_km, shift)
        best = (dv, miss, pc)
    return best


def run(maneuver_input: dict) -> dict:
    """
    Expected fields (build plan §5 contract, plus orchestrator context
    enrichment the real agent is free to ignore):
        primary_id, risk_tier, pc, tca_utc, current_state, mean_motion_rad_s,
        pc_threshold_target, and (context) miss_distance_km, burn_lead_time_s.
    """
    from app.agents.orbital_math import parse_iso, iso
    from datetime import timedelta

    primary_id = maneuver_input["primary_id"]
    pc0 = maneuver_input.get("pc", 1e-3)
    miss0_km = maneuver_input.get("miss_distance_km", 1.0)
    n_rad_s = maneuver_input.get("mean_motion_rad_s", 0.0011)
    pc_target = maneuver_input.get("pc_threshold_target", 1e-6)
    tca = parse_iso(maneuver_input["tca_utc"])
    lead_time_s = maneuver_input.get("burn_lead_time_s", 46 * 60)
    burn_time = tca - timedelta(seconds=lead_time_s)

    candidates = []
    for m_idx, direction in enumerate(["along_track", "radial", "cross_track"], start=1):
        dv, miss, pc = _search_direction(direction, pc0, miss0_km, lead_time_s, n_rad_s, pc_target)
        candidates.append({
            "maneuver_id": f"M{m_idx}",
            "burn_time_utc": iso(burn_time),
            "direction": direction,
            "dv_ms": round(dv, 3),
            "predicted_miss_km": round(miss, 3),
            "predicted_pc": pc,
        })

    candidates.sort(key=lambda c: c["dv_ms"])
    return {"primary_id": primary_id, "candidates": candidates}
