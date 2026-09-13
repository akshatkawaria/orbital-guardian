"""
Fixture-driven integration tests. These exercise evaluate_candidates() end
to end — exactly what the Decision Agent will call — and pin down the
headline demo scenario from implementation-plan §5/§3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mission_constraint_agent.agent import evaluate_candidates
from mission_constraint_agent.config import MissionConstraintConfig
from mission_constraint_agent.state_provider import InMemoryStateProvider

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def _provider_from_fixture(fixture: dict) -> InMemoryStateProvider:
    provider = InMemoryStateProvider()
    ps = fixture["primary_state"]
    provider.register(ps["object_id"], ps["r_km"], ps["v_kms"], ps["mean_motion_rad_s"])
    return provider


def test_demo_scenario_matches_build_plan_target():
    """
    The headline test: reproduces Orbital_Guardian_Build_Plan.md §6's exact
    demo target — M1 clean approval, M2 altitude violation, M3 double
    violation including the secondary-conjunction catch on SAT-107. This is
    the "watch it reject the maneuver that would hit SAT-107" beat from the
    build order table (§'Build order', phase 6).
    """
    fixture = _load_fixture("demo_scenario.json")
    provider = _provider_from_fixture(fixture)

    result = evaluate_candidates(
        fixture["request"], state_provider=provider, config=MissionConstraintConfig.demo_scale()
    )

    assert result == fixture["expected_response"]


def test_demo_scenario_m3_is_rejected_for_the_right_reason():
    """
    Narrower assertion than the full-equality test above: specifically
    pins down that M3's rejection includes the secondary-conjunction code,
    since that's the single most important behavior this agent has to get
    right for the demo to land.
    """
    fixture = _load_fixture("demo_scenario.json")
    provider = _provider_from_fixture(fixture)

    result = evaluate_candidates(
        fixture["request"], state_provider=provider, config=MissionConstraintConfig.demo_scale()
    )

    m3 = next(row for row in result["evaluated"] if row["maneuver_id"] == "M3")
    assert m3["approved"] is False
    assert "creates_secondary_conjunction:SAT-107" in m3["violations"]


def test_ground_region_fixture_isolates_that_check():
    fixture = _load_fixture("ground_region_violation.json")
    provider = _provider_from_fixture(fixture)

    result = evaluate_candidates(fixture["request"], state_provider=provider)

    assert result == fixture["expected_response"]


def test_output_has_one_row_per_input_candidate():
    """Contract-level regression test (implementation-plan §5, item 5):
    nothing silently dropped, every maneuver_id appears exactly once."""
    fixture = _load_fixture("demo_scenario.json")
    provider = _provider_from_fixture(fixture)

    result = evaluate_candidates(
        fixture["request"], state_provider=provider, config=MissionConstraintConfig.demo_scale()
    )

    input_ids = [c["maneuver_id"] for c in fixture["request"]["candidates"]]
    output_ids = [row["maneuver_id"] for row in result["evaluated"]]
    assert sorted(input_ids) == sorted(output_ids)
    assert len(output_ids) == len(set(output_ids))  # no duplicates


def test_unknown_primary_id_raises_clear_error():
    fixture = _load_fixture("demo_scenario.json")
    empty_provider = InMemoryStateProvider()  # nothing registered

    with pytest.raises(LookupError):
        evaluate_candidates(fixture["request"], state_provider=empty_provider)
