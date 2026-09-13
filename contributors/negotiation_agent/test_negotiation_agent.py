import pytest
from negotiation_agent import negotiate, Proposal, UnresolvableConjunction

CID = "SAT-042_vs_SAT-107"


def test_case1_clear_winner_matches_build_plan_example():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.7, 62),
        Proposal("SAT-107", 0.4, 18),
    ])
    assert result["mover"] == "SAT-107"
    assert result["rationale"] == "lower_dv_required"
    assert result["dv_ms"] == 0.4
    assert result["runner_up"] == {"object_id": "SAT-042", "dv_ms": 0.7}


def test_case2_reversed():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.3, 20),
        Proposal("SAT-107", 0.9, 80),
    ])
    assert result["mover"] == "SAT-042"
    assert result["rationale"] == "lower_dv_required"


def test_case3_near_tie_a_has_more_fuel():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.50, 70),
        Proposal("SAT-107", 0.51, 30),
    ])
    assert result["mover"] == "SAT-042"
    assert result["rationale"] == "tiebreak_lower_fuel_reserve"


def test_case4_near_tie_b_has_more_fuel():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.50, 20),
        Proposal("SAT-107", 0.505, 90),
    ])
    assert result["mover"] == "SAT-107"
    assert result["rationale"] == "tiebreak_lower_fuel_reserve"


def test_case5_only_one_capable():
    result = negotiate(CID, [
        Proposal("SAT-042", None, 40),
        Proposal("SAT-107", 0.6, 40),
    ])
    assert result["mover"] == "SAT-107"
    assert result["rationale"] == "only_one_capable"


def test_case5b_only_one_capable_other_side():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.6, 40),
        Proposal("SAT-107", None, 40),
    ])
    assert result["mover"] == "SAT-042"
    assert result["rationale"] == "only_one_capable"


def test_case6_exact_tie_deterministic_fallback():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.5, 50),
        Proposal("SAT-107", 0.5, 50),
    ])
    # lexicographic first object_id wins
    assert result["mover"] == "SAT-042"
    assert result["rationale"] == "tiebreak_lower_fuel_reserve"


def test_case7_neither_capable_raises():
    with pytest.raises(UnresolvableConjunction):
        negotiate(CID, [
            Proposal("SAT-042", None, 40),
            Proposal("SAT-107", None, 40),
        ])


def test_rejects_wrong_number_of_proposals():
    with pytest.raises(ValueError):
        negotiate(CID, [Proposal("SAT-042", 0.5, 50)])


def test_epsilon_boundary_exactly_at_threshold_is_tie():
    # diff safely within DV_EPSILON_MS (0.02) should be treated as a tie
    result = negotiate(CID, [
        Proposal("SAT-042", 0.50, 90),
        Proposal("SAT-107", 0.519, 10),
    ])
    assert result["rationale"] == "tiebreak_lower_fuel_reserve"
    assert result["mover"] == "SAT-042"  # more fuel


def test_epsilon_boundary_just_outside_threshold_is_not_tie():
    result = negotiate(CID, [
        Proposal("SAT-042", 0.50, 10),
        Proposal("SAT-107", 0.531, 90),
    ])
    assert result["rationale"] == "lower_dv_required"
    assert result["mover"] == "SAT-042"
