"""
The four constraint checks from Orbital_Guardian_Build_Plan.md §6, run in
cheap-to-expensive order. Each function is pure and independently unit
testable — no shared state, no side effects, no ML/LLM calls, per the build
plan's explicit "keep this deterministic and auditable" directive.

Every check returns either None (passed) or violation code string(s).
Violation codes are accumulated by the caller (agent.py) rather than
short-circuited, so a candidate can fail on multiple independent grounds —
see the M3 example in the build plan's own output sample.
"""

from __future__ import annotations

from .config import MissionConstraintConfig
from .models import ManeuverCandidate, MissionConstraints
from .orbital_math import (
    estimate_ground_track_shift_km,
    min_separation_to_fixed_object,
    post_maneuver_altitude_range,
)
from .state_provider import PrimaryState


def check_fuel(candidate: ManeuverCandidate, constraints: MissionConstraints) -> str | None:
    """§2.1 — trivial arithmetic, but sets the pattern for every other check."""
    if candidate.fuel_cost_pct is None:
        return None  # nothing to check if the Maneuver Agent didn't populate this yet
    if candidate.fuel_cost_pct > constraints.max_fuel_budget_pct_remaining:
        return "fuel_budget_exceeded"
    return None


def check_altitude_band(
    candidate: ManeuverCandidate,
    primary_state: PrimaryState,
    constraints: MissionConstraints,
) -> str | None:
    """
    §2.2 — first-order Δa estimate from the Gauss variational equations,
    checked against the declared station-keeping band. Fast screening
    approximation; the finally-selected candidate should still be confirmed
    via full SGP4 re-propagation before execution (see implementation-plan
    §2.2 fidelity note).
    """
    lo, hi = constraints.altitude_band_km
    perigee_alt, apogee_alt = post_maneuver_altitude_range(
        primary_state["r_km"], primary_state["mean_motion_rad_s"], candidate.dv_ms, candidate.direction
    )
    if apogee_alt > hi or perigee_alt < lo:
        return "altitude_band_exceeded"
    return None


def check_ground_region(
    candidate: ManeuverCandidate,
    constraints: MissionConstraints,
    config: MissionConstraintConfig,
) -> str | None:
    """
    §2.3 Tier-1 heuristic. Returns a parameterized violation code naming the
    first protected region at risk (extend to report all regions if the
    project needs multi-region granularity in the demo).
    """
    if not constraints.protected_ground_regions:
        return None
    shift_km = estimate_ground_track_shift_km(
        candidate.dv_ms, candidate.direction, config.ground_region_horizon_hours
    )
    if shift_km > config.ground_region_tolerance_km:
        region = constraints.protected_ground_regions[0]
        return f"protected_region_coverage_risk:{region}"
    return None


def check_secondary_conjunctions(
    candidate: ManeuverCandidate,
    primary_state: PrimaryState,
    constraints: MissionConstraints,
    config: MissionConstraintConfig,
) -> list[str]:
    """
    §2.4 — the demo-critical check. Projects the maneuvered primary via the
    Clohessy-Wiltshire equations and flags any other tracked/active object
    the projected trajectory comes within the screening threshold of.
    """
    violations: list[str] = []
    for other in constraints.other_active_satellites_km:
        min_sep_km = min_separation_to_fixed_object(
            primary_r_km=primary_state["r_km"],
            primary_v_kms=primary_state["v_kms"],
            other_r_km=other.r_km,
            dv_ms=candidate.dv_ms,
            direction=candidate.direction,
            mean_motion_rad_s=primary_state["mean_motion_rad_s"],
            screening_window_s=config.secondary_screening_window_s,
            step_s=config.secondary_screening_step_s,
        )
        if min_sep_km < config.secondary_conjunction_danger_threshold_km:
            violations.append(f"creates_secondary_conjunction:{other.object_id}")
    return violations
