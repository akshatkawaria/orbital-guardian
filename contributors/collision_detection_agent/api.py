"""
FastAPI wrapper for the Collision Detection Agent.

Matches the internal API shape defined in Orbital_Guardian_Build_Plan.md
§10 (Dashboard / Orchestration API):

    GET /conjunctions   -> Collision Detection Agent output

Plus a POST variant that accepts an arbitrary ephemeris payload, which is
what you actually want when this agent is called programmatically by the
Prediction Agent's output (rather than always reading the last cached
result) or when another teammate is testing against a fixture file
without wiring up the full pipeline yet.

Run standalone for local development:
    pip install fastapi uvicorn --break-system-packages
    uvicorn api:app --reload --port 8003

Then, e.g.:
    curl -X POST http://localhost:8003/conjunctions -H "Content-Type: application/json" -d @fixture_request.json
    curl http://localhost:8003/conjunctions   # returns the most recent result
    curl http://localhost:8003/health
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import CollisionDetectionError, run_collision_detection

app = FastAPI(
    title="Orbital Guardian - Collision Detection Agent",
    description="Filter-cascade / KD-tree conjunction screener. "
                "Consumes Prediction Agent ephemerides, emits candidate "
                "conjunctions (TCA + miss distance) for the Risk Assessment Agent.",
    version="1.0.0",
)

# In-memory cache of the most recent result, so a plain GET (matching the
# Dashboard's documented API shape) has something to return without
# requiring every caller to re-POST the full ephemeris payload. A real
# deployment would back this with the shared state DB / message bus other
# agents write to -- this is a hackathon-appropriate stand-in.
_last_result: dict[str, Any] | None = None
_last_request_at: str | None = None


class ScreeningWindow(BaseModel):
    start_utc: str
    end_utc: str


class EphemerisSample(BaseModel):
    t_utc: str
    r_km: list[float] = Field(..., min_length=3, max_length=3)
    v_kms: list[float] = Field(..., min_length=3, max_length=3)


class ObjectEphemeris(BaseModel):
    object_id: str
    frame: str
    ephemeris: list[EphemerisSample]


class CollisionDetectionRequest(BaseModel):
    screening_window: ScreeningWindow
    ephemerides: list[ObjectEphemeris]
    object_types: dict[str, str] | None = None


class AgentConfig(BaseModel):
    screening_radius_km: float | None = None
    pad_for_aliasing: bool | None = None
    max_rel_vel_kms: float | None = None
    fine_window_s: float | None = None
    fine_step_s: float | None = None
    cluster_gap_s: float | None = None


class ScreenRequest(BaseModel):
    request: CollisionDetectionRequest
    config: AgentConfig | None = None


@app.get("/health")
def health():
    return {"status": "ok", "agent": "collision_detection", "version": "1.0.0"}


@app.post("/conjunctions")
def post_conjunctions(payload: ScreenRequest):
    """
    Run the Collision Detection Agent on a fresh ephemeris payload
    (typically the Prediction Agent's batched output for the current
    screening window) and cache the result for subsequent GETs.
    """
    global _last_result, _last_request_at

    request_dict = payload.request.model_dump()
    config_dict = payload.config.model_dump(exclude_none=True) if payload.config else None

    try:
        result = run_collision_detection(request_dict, config=config_dict)
    except CollisionDetectionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    _last_result = result
    _last_request_at = datetime.now(timezone.utc).isoformat()
    return result


@app.get("/conjunctions")
def get_conjunctions():
    """
    Return the most recently computed conjunction set, per the Dashboard's
    documented `GET /conjunctions` shape. Returns an empty result (not an
    error) if the agent hasn't been run yet, so the Dashboard can render
    an empty-state table on first load.
    """
    if _last_result is None:
        return {
            "conjunctions": [],
            "screening_summary": None,
            "note": "No screening run yet -- POST /conjunctions with a Prediction Agent payload first.",
        }
    return {**_last_result, "computed_at": _last_request_at}
