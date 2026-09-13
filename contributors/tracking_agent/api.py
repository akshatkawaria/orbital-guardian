"""
api.py — exposes the Tracking Agent over HTTP, matching the relevant
slice of the orchestration API shape from
Orbital_Guardian_Build_Plan.md sec. 10:

    POST /catalog/refresh   -> triggers Tracking Agent
    (the build plan's other routes belong to later-phase agents and are
    not implemented here)

Run with:  uvicorn tracking_agent.api:app --reload --port 8001

Other agents (Prediction Agent, Dashboard) consume this over HTTP by
calling GET /catalog and getting back exactly the JSON contract array
defined in the build plan -- no shared in-memory state required, which
is the whole point of the "every arrow is a JSON contract" design rule.
"""

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent import TrackingAgent
from .parser import TLEParseError

app = FastAPI(title="Orbital Guardian — Tracking Agent")
_agent = TrackingAgent(db_path="tracking_catalog.db")


class RefreshRequest(BaseModel):
    source: str  # "celestrak" | "synthetic" | "spacetrack"
    group: Optional[str] = None          # celestrak: e.g. "active", "stations"
    catnr: Optional[int] = None          # celestrak/spacetrack: single object
    intdes: Optional[str] = None         # celestrak: one launch
    n_objects: Optional[int] = 50        # synthetic
    n_conjunction_pairs: Optional[int] = 3  # synthetic


class ManualTLERequest(BaseModel):
    object_id: str
    line1: str
    line2: str
    object_type: Optional[str] = None
    object_name: Optional[str] = None


@app.post("/catalog/refresh")
def refresh_catalog(req: RefreshRequest):
    """Trigger a catalog refresh from the requested source. Returns a
    write/reject count so the dashboard's agent-activity log has
    something concrete to display per the build plan's design intent."""
    try:
        if req.source == "synthetic":
            result = _agent.refresh_from_synthetic(
                n_objects=req.n_objects, n_conjunction_pairs=req.n_conjunction_pairs
            )
        elif req.source == "celestrak":
            if req.group:
                result = _agent.refresh_from_celestrak_group(req.group)
            elif req.catnr:
                record = _agent.refresh_from_celestrak_catnr(req.catnr)
                result = {"written": 1 if record else 0, "rejected_stale": 0}
            elif req.intdes:
                result = _agent.refresh_from_celestrak_intdes(req.intdes)
            else:
                raise HTTPException(400, "celestrak source requires group, catnr, or intdes")
        elif req.source == "spacetrack":
            result = _agent.refresh_from_spacetrack_recent()
        else:
            raise HTTPException(400, f"unknown source: {req.source}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"refresh failed: {exc}") from exc

    return {"source": req.source, **result, "catalog_size": _agent.stats()["total_objects"]}


@app.post("/catalog/ingest")
def ingest_tle(req: ManualTLERequest):
    """Ingest a single hand-supplied TLE (e.g. an operator paste-box)."""
    try:
        record = _agent.ingest_raw_tle(
            object_id=req.object_id, line1=req.line1, line2=req.line2,
            object_type=req.object_type, object_name=req.object_name,
        )
    except TLEParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    return record


@app.get("/catalog")
def list_catalog(object_type: Optional[str] = None):
    """The full current-state feed, in the exact contract shape the
    Prediction Agent's input schema expects."""
    return _agent.get_catalog(object_type=object_type)


@app.get("/catalog/{object_id}")
def get_object(object_id: str):
    record = _agent.get_object(object_id)
    if record is None:
        raise HTTPException(404, f"no object with id {object_id}")
    return record


@app.get("/catalog/stats/summary")
def catalog_stats():
    return _agent.stats()
