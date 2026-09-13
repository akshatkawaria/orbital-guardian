"""
Per-check unit tests: isolate one constraint at a time, per
implementation-plan §5's testing strategy. Each test constructs the
smallest possible candidate/constraints pair needed to exercise exactly
one check.
"""

from __future__ import annotations

import pytest

from mission_constraint_agent.checks import (
    check_altitude_band,
    check_fuel,
    check_ground_region,
    check_secondary_conjunctions,
)
from mission_constraint_agent.config import MissionConstraintConfig
from mission_constraint_agent.models import ManeuverCandidate, MissionConstraints, OtherActiveSatellite

A_550KM = 6928.137
PRIMARY_STATE = {
    "r_km": (A_550KM, 0.0, 0.0),
    "v_kms": (0.0, 7.585088535158763, 0.0),
    "mean_motion_rad_s": 0.001094823692885802,
}


def _candidate(**overrides) -> ManeuverCandidate:
    base = dict(
        maneuver_id="TEST",
        burn_time_utc="2026-09-12T13:46:00Z",
        direction="along_track",
        dv_ms=0.72,
        predicted_miss_km=16.8,
        predicted_pc=4.1e-7,
        fuel_cost_pct=0.3,
    )
    base.update(overrides)
    return ManeuverCandidate(**base)


def _constraints(**overrides) -> MissionConstraints:
    base = dict(
        altitude_band_km=(400.0, 900.0),
        protected_ground_regions=[],
        max_fuel_budget_pct_remaining=5.0,
        other_active_satellites_km=[],
    )
    base.update(overrides)
    return MissionConstraints(**base)


# --- fuel ------------------------------------------------------------------

def test_fuel_check_passes_within_budget():
    candidate = _candidate(fuel_cost_pct=0.3)
    constraints = _constraints(max_fuel_budget_pct_remaining=0.5)
    assert check_fuel(candidate, constraints) is None


def test_fuel_check_flags_over_budget():
    candidate = _candidate(fuel_cost_pct=0.6)
    constraints = _constraints(max_fuel_budget_pct_remaining=0.5)
    assert check_fuel(candidate, constraints) == "fuel_budget_exceeded"


def test_fuel_check_boundary_is_inclusive_pass():
    """Exactly at budget should pass, not fail — pins down the < vs <= choice."""
    candidate = _candidate(fuel_cost_pct=0.5)
    constraints = _constraints(max_fuel_budget_pct_remaining=0.5)
    assert check_fuel(candidate, constraints) is None


# --- altitude band -----------------------------------------------------------

def test_altitude_check_passes_for_small_along_track_burn():
    candidate = _candidate(direction="along_track", dv_ms=0.72)
    constraints = _constraints(altitude_band_km=(548.0, 552.0))
    assert check_altitude_band(candidate, PRIMARY_STATE, constraints) is None


def test_altitude_check_flags_larger_along_track_burn():
    candidate = _candidate(direction="along_track", dv_ms=0.91)
    constraints = _constraints(altitude_band_km=(548.0, 552.0))
    assert check_altitude_band(candidate, PRIMARY_STATE, constraints) == "altitude_band_exceeded"


def test_altitude_check_treats_radial_burn_as_no_altitude_change():
    """Radial burns don't change semi-major axis to first order (§2.2)."""
    candidate = _candidate(direction="radial", dv_ms=5.0)
    constraints = _constraints(altitude_band_km=(548.0, 552.0))
    assert check_altitude_band(candidate, PRIMARY_STATE, constraints) is None


# --- ground region -----------------------------------------------------------

def test_ground_region_check_skips_when_no_regions_declared():
    candidate = _candidate(direction="along_track", dv_ms=50.0)
    constraints = _constraints(protected_ground_regions=[])
    assert check_ground_region(candidate, constraints, MissionConstraintConfig()) is None


def test_ground_region_check_flags_large_along_track_drift():
    candidate = _candidate(direction="along_track", dv_ms=50.0)
    constraints = _constraints(protected_ground_regions=["India"])
    result = check_ground_region(candidate, constraints, MissionConstraintConfig())
    assert result == "protected_region_coverage_risk:India"


def test_ground_region_check_passes_small_burn_under_demo_scale_config():
    candidate = _candidate(direction="along_track", dv_ms=0.91)
    constraints = _constraints(protected_ground_regions=["India"])
    result = check_ground_region(candidate, constraints, MissionConstraintConfig.demo_scale())
    assert result is None


def test_ground_region_check_never_flags_radial_burns_tier1():
    """Tier-1 heuristic has no drift model for radial/cross-track burns
    (§2.3) — documents the known blind spot rather than hiding it."""
    candidate = _candidate(direction="radial", dv_ms=500.0)
    constraints = _constraints(protected_ground_regions=["India"])
    assert check_ground_region(candidate, constraints, MissionConstraintConfig()) is None


# --- secondary conjunction ---------------------------------------------------

def test_secondary_conjunction_check_empty_when_no_other_satellites():
    candidate = _candidate(direction="radial", dv_ms=1.20)
    constraints = _constraints(other_active_satellites_km=[])
    assert check_secondary_conjunctions(candidate, PRIMARY_STATE, constraints, MissionConstraintConfig()) == []


def test_secondary_conjunction_check_flags_close_projected_pass():
    candidate = _candidate(direction="radial", dv_ms=1.20)
    constraints = _constraints(
        other_active_satellites_km=[OtherActiveSatellite(object_id="SAT-107", r_km=(6924.437, -5.05, 0.0))]
    )
    violations = check_secondary_conjunctions(candidate, PRIMARY_STATE, constraints, MissionConstraintConfig())
    assert violations == ["creates_secondary_conjunction:SAT-107"]


def test_secondary_conjunction_check_clears_a_distant_object():
    candidate = _candidate(direction="along_track", dv_ms=0.72)
    constraints = _constraints(
        other_active_satellites_km=[OtherActiveSatellite(object_id="SAT-999", r_km=(0.0, 0.0, 6928.137))]
    )
    violations = check_secondary_conjunctions(candidate, PRIMARY_STATE, constraints, MissionConstraintConfig())
    assert violations == []
