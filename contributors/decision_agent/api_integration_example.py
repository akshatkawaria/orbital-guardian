"""
Reference integration for the Dashboard / Orchestration API (build plan §10).

This is intentionally NOT wired into a real database/event bus — it's a
minimal, runnable example your teammate building the FastAPI dashboard can
copy from directly. It shows:

  - GET  /decision/{primary}   -> last Decision Agent output for that primary
  - POST /maneuvers/{primary}  -> the chained call: Maneuver + Mission
                                   Constraint + Decision Agents (the latter
                                   two stubbed here as fixture lookups since
                                   this file's only job is to demonstrate the
                                   Decision Agent's integration surface)
  - GET  /agent-log             -> the timestamped event stream the build
                                    plan calls for, populated from decision
                                    events so the "Agent Activity" panel has
                                    real content to show

Run it:
    pip install fastapi uvicorn
    uvicorn api_integration_example:app --reload
Then:
    curl -X POST http://localhost:8000/maneuvers/SAT-042 \
         -H "Content-Type: application/json" \
         -d @fixtures/03_red_maneuver_required.json
    curl http://localhost:8000/decision/SAT-042
    curl http://localhost:8000/agent-log
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

from decision_agent import DecisionAgent
from decision_agent.models import SchemaError

try:
    from fastapi import FastAPI, HTTPException
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "This example needs FastAPI: pip install fastapi uvicorn"
    ) from e


app = FastAPI(title="Orbital Guardian — Decision Agent integration example")

# A single shared DecisionAgent instance gives you hysteresis "for free"
# across requests within this process, per conjunction_id (see
# DecisionAgent.__init__ docstring re: multi-process deployments).
_agent = DecisionAgent()

# Toy in-memory stores standing in for the real state DB (Tracking Agent's
# database, per §1 of the build plan) and the event log (§10).
_last_decision_by_primary: Dict[str, Dict[str, Any]] = {}
_agent_log: List[Dict[str, Any]] = []


def _log_event(agent: str, message: str) -> None:
    _agent_log.append(
        {
            "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent": agent,
            "message": message,
        }
    )


@app.post("/maneuvers/{primary_id}")
def run_decision_pipeline(primary_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    In the real system this endpoint chains Maneuver Agent -> Mission
    Constraint Agent -> Decision Agent (build plan §10). Here we accept the
    Decision Agent's already-merged input shape directly (matching a
    fixture file) since Agents 5/6 aren't this file's concern — swap the
    body of this function for real calls to those agents' modules once
    they're ready; the Decision Agent call below doesn't change.
    """
    payload = {**payload, "primary_id": primary_id}
    try:
        result = _agent.decide(payload, track_state=True)
    except SchemaError as e:
        _log_event("decision_agent", f"REJECTED malformed input for {primary_id}: {e}")
        raise HTTPException(status_code=422, detail=str(e))

    _last_decision_by_primary[primary_id] = result
    _log_event(
        "decision_agent",
        f"{primary_id}: {result['decision']} — {result['decision_reason']}",
    )
    return result


@app.get("/decision/{primary_id}")
def get_last_decision(primary_id: str) -> Dict[str, Any]:
    if primary_id not in _last_decision_by_primary:
        raise HTTPException(status_code=404, detail=f"no decision on record for {primary_id}")
    return _last_decision_by_primary[primary_id]


@app.get("/agent-log")
def get_agent_log() -> List[Dict[str, Any]]:
    return _agent_log
