"""
Unit tests for the Decision Agent.

Each test is a self-contained fixture dict matching the merged input schema
(README §Input schema) — no dependency on Agents 1-6 actually running.
This is the "independent development" property the whole project architecture
is built around: these tests would have passed on day one, before any other
agent existed.
"""

import pytest

from decision_agent import DecisionAgent


def make_input(**overrides):
    base = {
        "primary_id": "SAT-042",
        "secondary_id": "DEB-891",
        "risk_tier": "RED",
        "pc": 3.4e-3,
        "time_to_tca_minutes": 46,
        "pc_threshold_target": 1e-6,
        "evaluated_candidates": [
            {
                "maneuver_id": "M1",
                "approved": True,
                "dv_ms": 0.72,
                "predicted_pc": 4.1e-7,
                "predicted_miss_km": 16.8,
                "fuel_cost_pct": 0.3,
                "burn_time_utc": "2026-09-12T13:46:00Z",
                "direction": "along_track",
                "violations": [],
            },
            {
                "maneuver_id": "M2",
                "approved": False,
                "dv_ms": 0.91,
                "predicted_pc": 1.2e-7,
                "predicted_miss_km": 23.2,
                "fuel_cost_pct": 0.4,
                "violations": ["altitude_band_exceeded"],
            },
            {
                "maneuver_id": "M3",
                "approved": False,
                "dv_ms": 1.20,
                "predicted_pc": 5.0e-8,
                "predicted_miss_km": 41.7,
                "fuel_cost_pct": 0.6,
                "violations": ["fuel_budget_exceeded", "creates_secondary_conjunction:SAT-107"],
            },
        ],
    }
    base.update(overrides)
    return base


# --- Happy paths per tier ------------------------------------------------

def test_green_tier_is_no_action():
    agent = DecisionAgent()
    out = agent.decide(make_input(risk_tier="GREEN", pc=1e-9, time_to_tca_minutes=500))
    assert out["decision"] == "NO_ACTION"
    assert out["selected_maneuver"] is None
    assert out["confidence_pct"] is None


def test_yellow_tier_is_monitor():
    agent = DecisionAgent()
    out = agent.decide(make_input(risk_tier="YELLOW", pc=5e-6, time_to_tca_minutes=500))
    assert out["decision"] == "MONITOR"
    assert out["selected_maneuver"] is None


def test_red_tier_with_approved_candidate_maneuvers():
    agent = DecisionAgent()
    out = agent.decide(make_input())
    assert out["decision"] == "MANEUVER_REQUIRED"
    assert out["selected_maneuver"]["maneuver_id"] == "M1"
    assert out["selected_maneuver"]["meets_pc_target"] is True
    assert out["confidence_pct"] is not None
    # Rejected candidates should be surfaced with their violation reasons.
    rejected_ids = {c["maneuver_id"] for c in out["rejected_candidates"]}
    assert rejected_ids == {"M2", "M3"}


# --- Selection rule: cheapest Δv wins -------------------------------------

def test_picks_lowest_dv_among_multiple_approved():
    candidates = [
        {"maneuver_id": "A", "approved": True, "dv_ms": 1.5, "predicted_pc": 1e-8},
        {"maneuver_id": "B", "approved": True, "dv_ms": 0.4, "predicted_pc": 2e-7},
        {"maneuver_id": "C", "approved": True, "dv_ms": 0.9, "predicted_pc": 9e-8},
    ]
    agent = DecisionAgent()
    out = agent.decide(make_input(evaluated_candidates=candidates))
    assert out["selected_maneuver"]["maneuver_id"] == "B"


def test_tiebreak_is_deterministic():
    """Equal dv_ms -> tie broken by lower predicted_pc, reproducibly."""
    candidates = [
        {"maneuver_id": "X", "approved": True, "dv_ms": 0.5, "predicted_pc": 3e-7},
        {"maneuver_id": "Y", "approved": True, "dv_ms": 0.5, "predicted_pc": 1e-7},
    ]
    agent = DecisionAgent()
    out1 = agent.decide(make_input(evaluated_candidates=candidates))
    out2 = agent.decide(make_input(evaluated_candidates=list(reversed(candidates))))
    assert out1["selected_maneuver"]["maneuver_id"] == "Y"
    assert out2["selected_maneuver"]["maneuver_id"] == "Y"  # order-independent


# --- Edge case (a): zero approved candidates -----------------------------

def test_red_tier_with_no_approved_candidates_escalates():
    candidates = [
        {
            "maneuver_id": "M2",
            "approved": False,
            "dv_ms": 0.91,
            "predicted_pc": 1.2e-7,
            "violations": ["altitude_band_exceeded"],
        },
    ]
    agent = DecisionAgent()
    out = agent.decide(make_input(evaluated_candidates=candidates))
    assert out["decision"] == "ESCALATE_NO_VIABLE_OPTION"
    assert out["selected_maneuver"] is None
    assert len(out["rejected_candidates"]) == 1


# --- Edge case (c): approved candidate exists but none meets the target --

def test_fallback_to_best_approved_when_none_meets_target():
    candidates = [
        {"maneuver_id": "M1", "approved": True, "dv_ms": 0.72, "predicted_pc": 5e-5},  # > target
        {"maneuver_id": "M2", "approved": True, "dv_ms": 1.10, "predicted_pc": 8e-5},  # worse, > target
    ]
    agent = DecisionAgent()
    out = agent.decide(make_input(evaluated_candidates=candidates, pc_threshold_target=1e-6))
    assert out["decision"] == "MANEUVER_REQUIRED"
    assert out["selected_maneuver"]["maneuver_id"] == "M1"  # still cheapest Δv
    assert out["selected_maneuver"]["meets_pc_target"] is False
    assert "falling back" in out["decision_reason"]


# --- Edge case (d): time-urgency bump -------------------------------------

def test_yellow_tier_bumped_to_monitor_escalation_note_when_tca_imminent():
    agent = DecisionAgent()
    out = agent.decide(
        make_input(risk_tier="YELLOW", pc=5e-6, time_to_tca_minutes=10, evaluated_candidates=[])
    )
    # YELLOW -> bumped one notch -> ORANGE, which (with zero candidates given)
    # routes into the maneuver-tier branch and finds nothing approved.
    assert out["decision"] == "ESCALATE_NO_VIABLE_OPTION"
    assert "escalated from YELLOW" in out["decision_reason"]


def test_green_tier_not_bumped_even_if_tca_imminent():
    """Time urgency should never manufacture risk that Pc doesn't support."""
    agent = DecisionAgent()
    out = agent.decide(
        make_input(risk_tier="GREEN", pc=1e-9, time_to_tca_minutes=5, evaluated_candidates=[])
    )
    assert out["decision"] == "NO_ACTION"


# --- Edge case (e): hysteresis --------------------------------------------

def test_hysteresis_holds_maneuver_required_against_minor_pc_jitter():
    agent = DecisionAgent()
    # Cycle 1: RED, maneuver required.
    out1 = agent.decide(make_input(), track_state=True)
    assert out1["decision"] == "MANEUVER_REQUIRED"

    # Cycle 2: Pc jitters down into YELLOW territory, but not by a full
    # order of magnitude below threshold -> hysteresis should hold.
    jittered = make_input(risk_tier="YELLOW", pc=8e-6)
    out2 = agent.decide(jittered, track_state=True)
    assert out2["decision"] == "MANEUVER_REQUIRED"
    assert "hysteresis" in out2["decision_reason"]


def test_hysteresis_releases_once_pc_drops_an_order_of_magnitude():
    agent = DecisionAgent()
    agent.decide(make_input(), track_state=True)  # cycle 1: MANEUVER_REQUIRED

    # Cycle 2: Pc has genuinely dropped well below threshold -> allow downgrade.
    recovered = make_input(risk_tier="GREEN", pc=1e-9, evaluated_candidates=[])
    out2 = agent.decide(recovered, track_state=True)
    assert out2["decision"] == "NO_ACTION"


def test_hysteresis_never_suppresses_an_upgrade():
    agent = DecisionAgent()
    agent.decide(make_input(risk_tier="YELLOW", pc=5e-6, evaluated_candidates=[]), track_state=True)
    out2 = agent.decide(make_input(), track_state=True)  # now RED with a candidate
    assert out2["decision"] == "MANEUVER_REQUIRED"


# --- Schema robustness -----------------------------------------------------

def test_accepts_build_plan_pre_filtered_approved_candidates_shape():
    """Back-compat with the build plan's literal `approved_candidates` field."""
    agent = DecisionAgent()
    raw = {
        "primary_id": "SAT-042",
        "risk_tier": "RED",
        "pc": 3.4e-3,
        "approved_candidates": [
            {"maneuver_id": "M1", "dv_ms": 0.72, "predicted_pc": 4.1e-7, "fuel_cost_pct": 0.3}
        ],
    }
    out = agent.decide(raw)
    assert out["decision"] == "MANEUVER_REQUIRED"
    assert out["selected_maneuver"]["maneuver_id"] == "M1"


def test_missing_required_field_raises_schema_error():
    from decision_agent.models import SchemaError

    agent = DecisionAgent()
    with pytest.raises(SchemaError):
        agent.decide({"primary_id": "SAT-042", "risk_tier": "RED"})  # missing pc


def test_invalid_risk_tier_raises_schema_error():
    from decision_agent.models import SchemaError

    agent = DecisionAgent()
    with pytest.raises(SchemaError):
        agent.decide(make_input(risk_tier="PURPLE"))
