from datetime import datetime, timezone

import pytest

from app.maneuvers import generate_candidates
from app.models import ManeuverRequest, StateVector
from app.ranking import rank_candidates


def _sample_request(**overrides) -> ManeuverRequest:
    base = dict(
        event_id="CONJ-001",
        satellite_id="SAT-A",
        debris_id="DEBRIS-07",
        reference_time=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
        time_to_closest_approach_s=2400,
        burn_lead_time_s=1800,
        satellite_state=StateVector(
            position_km=[7000.0, 0.0, 0.0], velocity_km_s=[0.0, 7.546, 0.0]
        ),
        debris_state=StateVector(
            position_km=[6999.0, 18.0, 0.2], velocity_km_s=[0.01, 7.50, -0.001]
        ),
        baseline_miss_distance_km=0.12,
        target_miss_distance_km=2.0,
        allowed_directions=[
            "prograde",
            "retrograde",
            "radial_out",
            "radial_in",
            "normal",
            "anti_normal",
        ],
        delta_v_options_m_s=[0.1, 0.2, 0.3, 0.4, 0.5],
        max_delta_v_m_s=0.5,
    )
    base.update(overrides)
    return ManeuverRequest(**base)


def test_generates_one_candidate_per_direction_dv_combo():
    req = _sample_request()
    result = generate_candidates(req)
    assert len(result.candidates) == len(req.allowed_directions) * len(
        req.delta_v_options_m_s
    )


def test_larger_delta_v_increases_miss_distance_for_a_controlled_along_track_case():
    # Purpose-built scenario where the physics is unambiguous: debris is
    # co-orbital with the satellite, slightly ahead in-track. A prograde
    # burn raises the satellite's orbit, slowing its angular rate, so it
    # secularly falls further behind (away from the debris) as delta-v
    # increases -- this should be monotonic, unlike an arbitrary
    # real-world conjunction geometry where radial/normal coupling can
    # make the dv-vs-miss-distance relationship non-monotonic (which is
    # precisely why the API explores a menu of candidates instead of
    # assuming monotonicity).
    import math

    a = 7000.0
    mu = 398600.4418
    v_circ = math.sqrt(mu / a)
    theta = 0.002  # debris slightly ahead in true anomaly

    req = _sample_request(
        time_to_closest_approach_s=2400,
        burn_lead_time_s=600,
        satellite_state=StateVector(position_km=[a, 0.0, 0.0], velocity_km_s=[0.0, v_circ, 0.0]),
        debris_state=StateVector(
            position_km=[a * math.cos(theta), a * math.sin(theta), 0.0],
            velocity_km_s=[-v_circ * math.sin(theta), v_circ * math.cos(theta), 0.0],
        ),
        allowed_directions=["prograde"],
        delta_v_options_m_s=[0.1, 0.5],
    )
    result = generate_candidates(req)
    by_dv = {c.delta_v_m_s: c.projected_miss_distance_km for c in result.candidates}
    assert by_dv[0.5] > by_dv[0.1]


def test_zero_burn_delta_v_options_rejected_by_model():
    with pytest.raises(Exception):
        _sample_request(delta_v_options_m_s=[0.0, 0.2])


def test_dv_exceeding_max_rejected_by_model():
    with pytest.raises(Exception):
        _sample_request(delta_v_options_m_s=[0.6], max_delta_v_m_s=0.5)


def test_burn_after_encounter_rejected_by_model():
    with pytest.raises(Exception):
        _sample_request(burn_lead_time_s=3000, time_to_closest_approach_s=2400)


def test_ranking_puts_successes_first_and_prefers_lowest_dv():
    req = _sample_request()
    result = generate_candidates(req)
    ranked, recommended_id = rank_candidates(
        result.candidates, req.max_delta_v_m_s, req.target_miss_distance_km
    )

    assert len(ranked) == len(result.candidates)
    assert ranked[0].candidate_id == recommended_id
    assert all(r.rank == i + 1 for i, r in enumerate(ranked))

    successes = [c for c in ranked if c.meets_target]
    failures = [c for c in ranked if not c.meets_target]
    # All successes must be ranked ahead of all failures.
    if successes and failures:
        assert max(c.rank for c in successes) < min(c.rank for c in failures)

    # Successes sorted by ascending delta-v.
    dvs = [c.delta_v_m_s for c in successes]
    assert dvs == sorted(dvs)


def test_ranking_recommends_best_improvement_when_none_meet_target():
    req = _sample_request(target_miss_distance_km=10_000.0)  # unreachable target
    result = generate_candidates(req)
    ranked, recommended_id = rank_candidates(
        result.candidates, req.max_delta_v_m_s, req.target_miss_distance_km
    )
    assert all(not c.meets_target for c in ranked)
    best_by_improvement = max(ranked, key=lambda c: c.improvement_km)
    assert recommended_id == best_by_improvement.candidate_id
    assert ranked[0].candidate_id == recommended_id


def test_deterministic_repeat_generation():
    req = _sample_request()
    result_a = generate_candidates(req)
    result_b = generate_candidates(req)
    miss_a = [c.projected_miss_distance_km for c in result_a.candidates]
    miss_b = [c.projected_miss_distance_km for c in result_b.candidates]
    assert miss_a == miss_b
