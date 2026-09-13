"""Regression checks for physical consistency and cross-agent integration."""
import asyncio
import math
import os
import tempfile
import time
from pathlib import Path
import numpy as np
import pytest

# Keep test runs away from the user's saved simulation database.
os.environ["ORBITAL_DB_PATH"]=str(Path(tempfile.mkdtemp(prefix="orbital-tests-"))/"test.db")
os.environ["LLM_PROVIDER"]="mock"
from app.state import store
from app.pipeline import run_full_pipeline,run_maneuver_chain
from app.integration.adapters import closest,REGISTRY
from contributors.risk_assessment_package.risk_assessment.chan_pc import chan_pc
from contributors.maneuver_agent.app.models import ManeuverRequest
from contributors.maneuver_agent.app.maneuvers import generate_candidates


@pytest.fixture(scope="module")
def result():
    store.init_db()
    return asyncio.run(run_full_pipeline(20,2,1))


def test_risk_matches_centered_gaussian():
    cov=(np.eye(3)*.01).tolist()
    pc=chan_pc([0,0,0],[0,1,0],15,cov,cov)["pc"]
    expected=1-math.exp(-.015**2/(2*.02))
    assert pc==pytest.approx(expected,rel=1e-8)


def test_degenerate_relative_velocity_rejected():
    with pytest.raises(ValueError):
        chan_pc([1,0,0],[0,0,0],15,np.eye(3),np.eye(3))


def test_tca_between_samples():
    a=lambda t:np.stack([np.asarray(t)-5.123,np.zeros_like(t)+.12,np.zeros_like(t)],axis=-1)
    b=lambda t:np.zeros(np.asarray(t).shape+(3,))
    miss,t=closest(a,b,0,20)
    assert t==pytest.approx(5.123,abs=1e-5)
    assert miss==pytest.approx(.12,abs=1e-6)


def test_all_agents_point_to_adapters():
    assert len(REGISTRY)==9
    assert all(fn.__module__=="app.integration.adapters" for fn in REGISTRY.values())


def test_pipeline_uses_team_modules(result):
    assert result["objects_tracked"]==20
    assert result["pairs_screened"]==190
    assert result["conjunctions_flagged"]>=1
    assert result["failures"]==[]
    assert any(r["risk_tier"]=="RED" for r in store.all_risks())


def test_selected_maneuver_and_trajectory(result):
    decision=next(d for d in result["decisions"] if d["selected_maneuver"])
    m=decision["selected_maneuver"]
    assert m["predicted_pc"]<1e-6
    assert m["improvement_km"]>0
    assert m["dv_ms"]>0
    original=store.get_ephemeris(decision["primary_id"])["ephemeris"][0]
    assert np.linalg.norm(np.array(m["trajectory"][0]["r_km"])-original["r_km"])<1e-8
    assert len(m["trajectory"])>=241


def test_report_available(result):
    for d in result["decisions"]:
        report=store.get_constraints(f"report:{d['primary_id']}")
        assert report["report_text"]
        assert report["source"] in ("mock","deterministic_fallback")


def test_no_fuel_escalates(result):
    primary=result["decisions"][0]["primary_id"]
    planned=asyncio.run(run_maneuver_chain(primary,constraints={"max_fuel_budget_pct_remaining":0}))
    assert planned["decision"]["decision"]=="ESCALATE_NO_VIABLE_OPTION"
    assert all(not c["approved"] for c in planned["evaluated"])


def test_negotiation():
    out=REGISTRY["negotiation"]({"conjunction_id":"test","proposals":[
        {"object_id":"A","min_dv_ms_to_clear":.4,"fuel_remaining_pct":50},
        {"object_id":"B","min_dv_ms_to_clear":.7,"fuel_remaining_pct":60}]})
    assert out["mover"]=="A"


def test_api_run_and_clean_repeat():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        assert client.get("/health").json()=={"status":"ok"}
        assert client.get("/").status_code==200
        assert client.post("/simulate/start",json={"object_count":1}).status_code==422
        for count in (6,4):
            assert client.post("/simulate/start",json={"object_count":count,"window_hours":1,"guaranteed_conjunctions":1}).status_code==200
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                status=client.get("/simulate/status").json()
                if status["status"]!="running":break
                time.sleep(.05)
            assert status["status"]=="completed",status
            snap=client.get("/snapshot").json()
            assert len(snap["catalog"])==count
            assert len(snap["ephemerides"])==count
            assert snap["reports"]
