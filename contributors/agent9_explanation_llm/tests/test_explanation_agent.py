import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402

os.environ["LLM_PROVIDER"] = "mock"  # force mock provider for all tests in this file

from app.explanation_agent import ExplanationAgent  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def test_generate_report_maneuver_required_is_grounded_and_readable():
    agent = ExplanationAgent(primary_provider="mock")
    fixture = load_fixture("decision_maneuver_required.json")

    text, source, fidelity_ok = agent.generate_report(fixture["decision"], fixture["context"])

    assert source == "mock"
    assert fidelity_ok
    assert "SAT-042" in text
    assert "DEB-891" in text
    assert "0.72" in text
    assert "16.8" in text


def test_generate_report_no_action_explains_why():
    agent = ExplanationAgent(primary_provider="mock")
    fixture = load_fixture("decision_no_action.json")

    text, source, fidelity_ok = agent.generate_report(fixture["decision"], fixture["context"])

    assert fidelity_ok
    assert "SAT-107" in text
    assert "no maneuver" in text.lower() or "no significant" in text.lower()


def test_parse_constraints_extracts_region_and_optimize_target():
    agent = ExplanationAgent(primary_provider="mock")
    with open(FIXTURES / "operator_commands.json") as f:
        cases = json.load(f)

    case = cases[0]  # India / min_fuel example straight from the build plan
    constraints, source = agent.parse_constraints(case["text"])

    assert source == "mock"
    assert "India" in constraints["protected_ground_regions"]
    assert constraints["optimize_for"] == "min_fuel"


def test_parse_constraints_extracts_altitude_band_and_fuel_budget():
    agent = ExplanationAgent(primary_provider="mock")
    with open(FIXTURES / "operator_commands.json") as f:
        cases = json.load(f)

    case = cases[2]  # altitude band + fuel budget example
    constraints, _source = agent.parse_constraints(case["text"])

    assert constraints["altitude_band_km"] == [540.0, 560.0]
    assert constraints["max_fuel_budget_pct_remaining"] == 2.0


def test_no_fallback_client_constructed_for_mock_provider():
    agent = ExplanationAgent(primary_provider="mock")
    assert agent.fallback is None
