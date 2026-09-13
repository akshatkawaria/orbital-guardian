import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import guardrails  # noqa: E402
from app.schemas import CONSTRAINT_JSON_SCHEMA  # noqa: E402


def test_numeric_fidelity_passes_for_grounded_report():
    payload = {
        "primary_id": "SAT-042",
        "selected_maneuver": {
            "dv_ms": 0.72,
            "predicted_miss_km": 16.8,
            "fuel_cost_pct": 0.3,
            "burn_time_utc": "2026-09-12T13:46:00Z",
        },
        "miss_distance_km": 1.8,
        "time_to_tca_minutes": 46,
    }
    report = (
        "SAT-042 has a high collision risk, closing to within 1.8 km in "
        "46 minutes. The recommended response is a 0.72 m/s along-track "
        "burn, which increases the miss distance to 16.8 km and costs "
        "0.3% of remaining fuel."
    )
    ok, unmatched = guardrails.numeric_fidelity_check(report, payload)
    assert ok, f"Expected pass, got unmatched: {unmatched}"


def test_numeric_fidelity_catches_hallucinated_number():
    payload = {
        "primary_id": "SAT-042",
        "selected_maneuver": {"dv_ms": 0.72, "predicted_miss_km": 16.8, "fuel_cost_pct": 0.3},
    }
    # 99.9 never appeared anywhere in the input -- this should fail.
    report = "SAT-042's maneuver costs 99.9% of remaining fuel."
    ok, unmatched = guardrails.numeric_fidelity_check(report, payload)
    assert not ok
    assert 99.9 in unmatched


def test_numeric_fidelity_allows_reasonable_rounding():
    payload = {"dv_ms": 0.723456}
    report = "A 0.72 m/s burn is recommended."
    ok, unmatched = guardrails.numeric_fidelity_check(report, payload)
    assert ok, f"Expected rounding tolerance to pass, got unmatched: {unmatched}"


def test_validate_constraints_accepts_well_formed_object():
    obj = {
        "protected_ground_regions": ["India"],
        "optimize_for": "min_fuel",
        "max_fuel_budget_pct_remaining": None,
        "altitude_band_km": None,
    }
    guardrails.validate_constraints(obj)  # should not raise


def test_validate_constraints_rejects_bad_enum():
    obj = {
        "protected_ground_regions": [],
        "optimize_for": "maximize_everything",  # not in enum
    }
    with pytest.raises(Exception):
        guardrails.validate_constraints(obj)


def test_deterministic_fallback_report_never_raises_and_contains_id():
    payload = {"primary_id": "SAT-999", "decision": "MANEUVER_REQUIRED", "selected_maneuver": {
        "maneuver_id": "M1", "direction": "along_track", "dv_ms": 1.0,
        "burn_time_utc": "2026-01-01T00:00:00Z", "predicted_miss_km": 10.0, "fuel_cost_pct": 1.0,
    }}
    text = guardrails.deterministic_fallback_report(payload)
    assert "SAT-999" in text


def test_constraint_schema_has_required_fields_present_in_pydantic_model():
    from app.schemas import ParsedConstraints

    fields = set(ParsedConstraints.model_fields.keys())
    schema_fields = set(CONSTRAINT_JSON_SCHEMA["properties"].keys())
    assert schema_fields.issubset(fields), "JSON schema and Pydantic model drifted apart"
