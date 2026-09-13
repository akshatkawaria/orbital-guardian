"""
FastAPI wrapper for the Prediction Agent.

Implements the `POST /propagate` route from the Dashboard/Orchestration
API shape in Orbital_Guardian_Build_Plan.md §10, plus a batch route for
when the Collision Detection Agent wants a whole screening window's worth
of objects at once. This is the integration seam other agents (or a
human testing the pipeline with curl/Postman) use -- the request/response
bodies are exactly the JSON contracts in models.py, so this file adds no
new schema of its own.

Run standalone with:
    uvicorn prediction_agent.api:app --reload --port 8001

Other agents in the pipeline call this the same way the Dashboard will:
    POST http://localhost:8001/propagate         (single object)
    POST http://localhost:8001/propagate/batch    (catalog window)
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from .batch import batch_propagate
from .core import propagate
from .models import (
    BatchPredictionOutput,
    BatchPredictionRequest,
    PredictionOutput,
    PredictionRequest,
)

app = FastAPI(
    title="Orbital Guardian - Prediction Agent",
    description="SGP4 orbit propagation, TEME->ECI_J2000 conversion. "
                "Consumes Tracking Agent records, produces ephemerides "
                "for the Collision Detection Agent.",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "prediction"}


@app.post("/propagate", response_model=PredictionOutput)
def propagate_one(request: PredictionRequest) -> PredictionOutput:
    """
    Single-object propagation. Matches the Tracking-Agent-record-in,
    ephemeris-out contract exactly. This is what a Tracking Agent
    refresh, or a dashboard "propagate this one object" action, calls.
    """
    try:
        return propagate(request)
    except Exception as exc:  # noqa: BLE001 - surface as a clean 500, don't leak a traceback to callers
        raise HTTPException(status_code=500, detail=f"propagation failed: {exc}") from exc


@app.post("/propagate/batch", response_model=BatchPredictionOutput)
def propagate_batch(request: BatchPredictionRequest) -> BatchPredictionOutput:
    """
    Catalog-scale propagation for the Collision Detection Agent's
    screening window: many objects, one shared time grid, vectorized via
    SatrecArray. Per-object failures are reported as warnings on that
    object's entry rather than failing the whole batch.
    """
    try:
        return batch_propagate(request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"batch propagation failed: {exc}") from exc
