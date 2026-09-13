"""
Test suite for the Collision Detection Agent.

Run with:  pytest test_agent.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from agent import (
    CollisionDetectionError,
    run_collision_detection,
    _build_interpolants,
)
from generate_fixture import generate_fixture


EPOCH = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _straight_line_ephemeris(object_id, r0, v, t_samples_s):
    """Constant-velocity straight-line trajectory -- lets us hand-derive the exact TCA."""
    ephemeris = []
    for t in t_samples_s:
        r = [r0[k] + v[k] * t for k in range(3)]
        ephemeris.append({
            "t_utc": _iso(EPOCH + timedelta(seconds=t)),
            "r_km": r,
            "v_kms": list(v),
        })
    return {"object_id": object_id, "frame": "ECI_J2000", "ephemeris": ephemeris}


# --------------------------------------------------------------------------
# 1. Analytic ground-truth test: two straight-line trajectories with a
#    hand-derivable closest-approach time and distance.
# --------------------------------------------------------------------------

def test_tca_matches_analytic_straight_line_case():
    # Object A: stationary at origin.
    # Object B: starts at (0, 100, 0) km, moves at (0, -1, 0) km/s -- i.e.
    # straight toward A, passing at zero miss distance at t=100s, then
    # continuing on. This is the simplest possible TCA case: closing
    # velocity is constant, so TCA is exactly 100s and miss distance is 0.
    t_samples = np.arange(0, 200, 10.0)  # coarse 10s cadence samples
    obj_a = _straight_line_ephemeris("A", r0=[0, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    obj_b = _straight_line_ephemeris("B", r0=[0, 100, 0], v=[0, -1, 0], t_samples_s=t_samples)

    request = {
        "screening_window": {"start_utc": _iso(EPOCH), "end_utc": _iso(EPOCH + timedelta(seconds=200))},
        "ephemerides": [obj_a, obj_b],
    }
    result = run_collision_detection(request, config={"screening_radius_km": 150.0})

    assert len(result["conjunctions"]) == 1
    c = result["conjunctions"][0]
    assert c["miss_distance_km"] < 1e-3
    tca_seconds = (
        datetime.fromisoformat(c["tca_utc"].replace("Z", "+00:00")) - EPOCH
    ).total_seconds()
    assert abs(tca_seconds - 100.0) < 0.5  # within one fine-scan step of the analytic answer


def test_offset_straight_line_case_has_nonzero_known_miss_distance():
    # Object B passes at a fixed 5 km lateral offset instead of dead-on.
    t_samples = np.arange(0, 200, 10.0)
    obj_a = _straight_line_ephemeris("A", r0=[0, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    obj_b = _straight_line_ephemeris("B", r0=[5, 100, 0], v=[0, -1, 0], t_samples_s=t_samples)

    request = {
        "screening_window": {"start_utc": _iso(EPOCH), "end_utc": _iso(EPOCH + timedelta(seconds=200))},
        "ephemerides": [obj_a, obj_b],
    }
    result = run_collision_detection(request, config={"screening_radius_km": 150.0})

    assert len(result["conjunctions"]) == 1
    c = result["conjunctions"][0]
    # Analytic answer: miss distance = 5 km exactly (perpendicular offset),
    # at t=100s (same as before -- only the perpendicular axis changed).
    assert abs(c["miss_distance_km"] - 5.0) < 1e-2
    tca_seconds = (
        datetime.fromisoformat(c["tca_utc"].replace("Z", "+00:00")) - EPOCH
    ).total_seconds()
    assert abs(tca_seconds - 100.0) < 0.5


# --------------------------------------------------------------------------
# 2. Contract validation
# --------------------------------------------------------------------------

def test_rejects_duplicate_object_ids():
    t_samples = np.arange(0, 100, 10.0)
    obj_a = _straight_line_ephemeris("DUP", r0=[0, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    obj_b = _straight_line_ephemeris("DUP", r0=[100, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    request = {
        "screening_window": {"start_utc": _iso(EPOCH), "end_utc": _iso(EPOCH + timedelta(seconds=100))},
        "ephemerides": [obj_a, obj_b],
    }
    with pytest.raises(CollisionDetectionError, match="duplicate object_id"):
        run_collision_detection(request)


def test_rejects_non_eci_frame():
    t_samples = np.arange(0, 100, 10.0)
    obj_a = _straight_line_ephemeris("A", r0=[0, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    obj_a["frame"] = "TEME"
    obj_b = _straight_line_ephemeris("B", r0=[100, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    request = {
        "screening_window": {"start_utc": _iso(EPOCH), "end_utc": _iso(EPOCH + timedelta(seconds=100))},
        "ephemerides": [obj_a, obj_b],
    }
    with pytest.raises(CollisionDetectionError, match="ECI_J2000"):
        run_collision_detection(request)


def test_missing_required_fields_raises():
    with pytest.raises(CollisionDetectionError):
        run_collision_detection({"ephemerides": []})


def test_fewer_than_two_objects_returns_empty_not_error():
    t_samples = np.arange(0, 100, 10.0)
    obj_a = _straight_line_ephemeris("A", r0=[0, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    request = {
        "screening_window": {"start_utc": _iso(EPOCH), "end_utc": _iso(EPOCH + timedelta(seconds=100))},
        "ephemerides": [obj_a],
    }
    result = run_collision_detection(request)
    assert result["conjunctions"] == []
    assert result["screening_summary"]["n_pairs_screened"] == 0


# --------------------------------------------------------------------------
# 3. Output shape matches the build-plan contract exactly
# --------------------------------------------------------------------------

def test_output_conjunction_has_all_contract_fields():
    t_samples = np.arange(0, 200, 10.0)
    obj_a = _straight_line_ephemeris("A", r0=[0, 0, 0], v=[0, 0, 0], t_samples_s=t_samples)
    obj_b = _straight_line_ephemeris("B", r0=[0, 100, 0], v=[0, -1, 0], t_samples_s=t_samples)
    request = {
        "screening_window": {"start_utc": _iso(EPOCH), "end_utc": _iso(EPOCH + timedelta(seconds=200))},
        "ephemerides": [obj_a, obj_b],
    }
    result = run_collision_detection(request, config={"screening_radius_km": 150.0})
    required_fields = {
        "primary_id", "secondary_id", "tca_utc", "miss_distance_km",
        "relative_velocity_kms", "relative_position_km", "relative_velocity_vec_kms",
    }
    for c in result["conjunctions"]:
        assert required_fields.issubset(c.keys())
        assert len(c["relative_position_km"]) == 3
        assert len(c["relative_velocity_vec_kms"]) == 3


# --------------------------------------------------------------------------
# 4. End-to-end funnel test against the synthetic fixture (the demo target)
# --------------------------------------------------------------------------

def test_funnel_demo_synthetic_fleet_flags_exactly_the_planted_pairs():
    request, planted_pairs = generate_fixture(
        n_objects=50, n_conjunctions=3, window_hours=24.0, step_s=60.0, seed=42
    )
    result = run_collision_detection(request, config={"screening_radius_km": 20.0})

    summary = result["screening_summary"]
    assert summary["n_pairs_screened"] == 50 * 49 // 2

    flagged_pairs = {
        frozenset([c["primary_id"], c["secondary_id"]]) for c in result["conjunctions"]
    }
    expected_pairs = {frozenset(p) for p in planted_pairs}
    assert flagged_pairs == expected_pairs, (
        f"Expected exactly the planted conjunctions {expected_pairs}, got {flagged_pairs}"
    )

    # every planted pair should show a small miss distance (they were placed
    # on near-identical orbital planes with a tiny phase offset)
    for c in result["conjunctions"]:
        assert c["miss_distance_km"] < 10.0


def test_pad_for_aliasing_never_flags_fewer_pairs_than_default():
    request, _ = generate_fixture(n_objects=30, n_conjunctions=2, seed=7)
    default_result = run_collision_detection(request)
    padded_result = run_collision_detection(request, config={"pad_for_aliasing": True})
    assert (
        padded_result["screening_summary"]["n_conjunctions_flagged"]
        >= default_result["screening_summary"]["n_conjunctions_flagged"]
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
