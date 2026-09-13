import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE_REQUEST = json.loads(
    (Path(__file__).parent.parent / "sample_request.json").read_text()
)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_generate_maneuvers_sample_request():
    resp = client.post("/maneuvers/generate", json=SAMPLE_REQUEST)
    assert resp.status_code == 200
    body = resp.json()

    assert body["event_id"] == SAMPLE_REQUEST["event_id"]
    n_expected = len(SAMPLE_REQUEST["allowed_directions"]) * len(
        SAMPLE_REQUEST["delta_v_options_m_s"]
    )
    assert len(body["candidates"]) == n_expected
    assert body["recommended_candidate_id"] is not None
    assert any("Agent 6" in w for w in body["warnings"])

    ranks = [c["rank"] for c in body["candidates"]]
    assert ranks == list(range(1, n_expected + 1))


def test_generate_maneuvers_is_deterministic_across_calls():
    resp1 = client.post("/maneuvers/generate", json=SAMPLE_REQUEST)
    resp2 = client.post("/maneuvers/generate", json=SAMPLE_REQUEST)
    assert resp1.json() == resp2.json()


def test_invalid_position_vector_returns_422():
    bad = json.loads(json.dumps(SAMPLE_REQUEST))
    bad["satellite_state"]["position_km"] = [1.0, 2.0]  # wrong length
    resp = client.post("/maneuvers/generate", json=bad)
    assert resp.status_code == 422


def test_burn_after_encounter_returns_422():
    bad = json.loads(json.dumps(SAMPLE_REQUEST))
    bad["burn_lead_time_s"] = bad["time_to_closest_approach_s"] + 100
    resp = client.post("/maneuvers/generate", json=bad)
    assert resp.status_code == 422


def test_unsupported_direction_returns_422():
    bad = json.loads(json.dumps(SAMPLE_REQUEST))
    bad["allowed_directions"] = ["sideways"]
    resp = client.post("/maneuvers/generate", json=bad)
    assert resp.status_code == 422


def test_delta_v_exceeding_max_returns_422():
    bad = json.loads(json.dumps(SAMPLE_REQUEST))
    bad["delta_v_options_m_s"] = [10.0]
    resp = client.post("/maneuvers/generate", json=bad)
    assert resp.status_code == 422
