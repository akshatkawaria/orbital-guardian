"""
Tests for the Risk Assessment Agent.

Since Chan's exact numeric output is hard to eyeball-verify by hand, these
tests focus on properties that MUST hold if the math is right:
  - Pc increases as miss distance shrinks (holding everything else fixed)
  - Pc increases as combined hard-body radius grows
  - Pc increases as covariance (uncertainty) grows, for a fixed miss distance
  - Pc is always in [0, 1]
  - risk tier classification boundaries are correct
  - a very safe, very risky, and a mid-risk scenario land in expected tiers
"""
import json
import os
from chan_pc import chan_pc
from risk_agent import assess_risk, classify_risk_tier

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_conjunction.json")


def _base_conjunction():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_pc_in_valid_range():
    conj = _base_conjunction()
    result = assess_risk(conj)
    assert 0.0 <= result["pc"] <= 1.0


def test_pc_increases_as_miss_distance_shrinks():
    cov_p = [[0.01, 0, 0], [0, 0.03, 0], [0, 0, 0.01]]
    cov_s = [[0.02, 0, 0], [0, 0.05, 0], [0, 0, 0.02]]
    v_rel = [7.9, -1.8, 1.2]
    hbr = 15

    far = chan_pc([2.0, -2.0, 1.5], v_rel, hbr, cov_p, cov_s)["pc"]
    close = chan_pc([0.2, -0.2, 0.1], v_rel, hbr, cov_p, cov_s)["pc"]
    assert close > far, "closer miss distance should mean higher Pc"


def test_pc_increases_with_larger_hbr():
    cov_p = [[0.01, 0, 0], [0, 0.03, 0], [0, 0, 0.01]]
    cov_s = [[0.02, 0, 0], [0, 0.05, 0], [0, 0, 0.02]]
    r_rel = [1.1, -0.9, 0.6]
    v_rel = [7.9, -1.8, 1.2]

    small_object = chan_pc(r_rel, v_rel, 5, cov_p, cov_s)["pc"]
    large_object = chan_pc(r_rel, v_rel, 50, cov_p, cov_s)["pc"]
    assert large_object > small_object, "a bigger combined object size should mean higher Pc"


def test_pc_increases_with_more_uncertainty_at_fixed_miss():
    """Counterintuitive but correct: if the miss distance is comfortably
    larger than the object size, MORE uncertainty (bigger covariance)
    actually smears probability mass onto the object, increasing Pc, up to
    a point. This is a well-known property of the Pc calculation and worth
    knowing so you don't 'fix' this behavior thinking it's a bug."""
    r_rel = [1.1, -0.9, 0.6]
    v_rel = [7.9, -1.8, 1.2]
    hbr = 15

    tight_cov = [[0.001, 0, 0], [0, 0.001, 0], [0, 0, 0.001]]
    loose_cov = [[0.05, 0, 0], [0, 0.05, 0], [0, 0, 0.05]]

    pc_tight = chan_pc(r_rel, v_rel, hbr, tight_cov, tight_cov)["pc"]
    pc_loose = chan_pc(r_rel, v_rel, hbr, loose_cov, loose_cov)["pc"]
    assert pc_loose > pc_tight, "more uncertainty at a fixed real miss distance should raise Pc here"


def test_risk_tier_thresholds():
    assert classify_risk_tier(5e-4) == "RED"
    assert classify_risk_tier(1e-4) == "RED"
    assert classify_risk_tier(5e-5) == "ORANGE"
    assert classify_risk_tier(1e-5) == "ORANGE"
    assert classify_risk_tier(5e-6) == "YELLOW"
    assert classify_risk_tier(1e-6) == "YELLOW"
    assert classify_risk_tier(1e-8) == "GREEN"


def test_high_risk_scenario_lands_in_red():
    """Deliberately dangerous: tiny miss distance, HBR comparable to the
    uncertainty scale -- should clearly land in RED."""
    conj = {
        "primary_id": "SAT-A", "secondary_id": "DEB-B",
        "tca_utc": "2026-09-12T14:32:00Z",
        "relative_position_km": [0.02, 0.01, 0.0],
        "relative_velocity_vec_kms": [7.5, 0.0, 0.0],
        "combined_hard_body_radius_m": 15,
        "position_covariance_primary_km2": [[0.0005, 0, 0], [0, 0.0005, 0], [0, 0, 0.0005]],
        "position_covariance_secondary_km2": [[0.0005, 0, 0], [0, 0.0005, 0], [0, 0, 0.0005]],
    }
    result = assess_risk(conj)
    assert result["risk_tier"] == "RED", f"expected RED, got {result['risk_tier']} (pc={result['pc']})"


def test_low_risk_scenario_lands_in_green():
    """Wide miss distance relative to uncertainty and object size -> should
    clearly land in GREEN."""
    conj = {
        "primary_id": "SAT-A", "secondary_id": "DEB-B",
        "tca_utc": "2026-09-12T14:32:00Z",
        "relative_position_km": [50.0, 30.0, 10.0],
        "relative_velocity_vec_kms": [7.5, 0.0, 0.0],
        "combined_hard_body_radius_m": 15,
        "position_covariance_primary_km2": [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 0.01]],
        "position_covariance_secondary_km2": [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 0.01]],
    }
    result = assess_risk(conj)
    assert result["risk_tier"] == "GREEN", f"expected GREEN, got {result['risk_tier']} (pc={result['pc']})"


def test_output_matches_contract_shape():
    conj = _base_conjunction()
    result = assess_risk(conj)
    assert set(result.keys()) == {
        "primary_id", "secondary_id", "pc", "pc_method",
        "risk_tier", "time_to_tca_minutes", "miss_distance_km",
    }
    assert result["pc_method"] == "Chan"
    assert result["risk_tier"] in {"GREEN", "YELLOW", "ORANGE", "RED"}


if __name__ == "__main__":
    tests = [
        test_pc_in_valid_range,
        test_pc_increases_as_miss_distance_shrinks,
        test_pc_increases_with_larger_hbr,
        test_pc_increases_with_more_uncertainty_at_fixed_miss,
        test_risk_tier_thresholds,
        test_high_risk_scenario_lands_in_red,
        test_low_risk_scenario_lands_in_green,
        test_output_matches_contract_shape,
    ]
    for t in tests:
        t()
        print(f"PASSED: {t.__name__}")
    print("\nAll tests passed.")
