"""
Agent 5: Maneuver Agent — FastAPI service.

Receives a predicted close-approach event (from Agents 3/4), generates
candidate avoidance maneuvers, simulates each with a simplified two-body
orbital model, and returns every candidate ranked deterministically.

This is a hackathon simulation only. It does not control, command, or
provide operational guidance for any real spacecraft.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .maneuvers import generate_candidates
from .models import HealthResponse, ManeuverRequest, ManeuverResponse
from .ranking import rank_candidates

app = FastAPI(
    title="Orbital Guardian — Maneuver Agent",
    description=(
        "Agent 5 of the Orbital Guardian pipeline. Generates and ranks "
        "candidate collision-avoidance maneuvers from a simplified "
        "two-body orbital model. SIMULATION ONLY — never controls or "
        "advises a real spacecraft."
    ),
    version="1.0.0",
)

# Local hackathon CORS: allow the frontend dev server to call this API
# from any origin. Not intended for production use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """Return helpful, structured 422 errors for invalid requests.

    Pydantic v2 error dicts can carry a non-JSON-serializable "ctx" entry
    (e.g. the original exception instance for a custom validator), so we
    strip that and keep only the human-readable, serializable fields.
    """
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": errors, "message": "Invalid maneuver request."},
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/maneuvers/generate", response_model=ManeuverResponse)
async def generate_maneuvers(req: ManeuverRequest) -> ManeuverResponse:
    try:
        result = generate_candidates(req)
    except ValueError as exc:
        # Physics-level validation failure discovered only after
        # propagating to burn time (e.g. degenerate frame mid-flight).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ranked, recommended_id = rank_candidates(
        raw_candidates=result.candidates,
        max_delta_v_m_s=req.max_delta_v_m_s,
        target_miss_distance_km=req.target_miss_distance_km,
    )

    warnings = [
        "Simulation result; Agent 6 must check mission constraints.",
        "Simplified two-body model: no J2/drag/luni-solar perturbations; "
        "impulsive burns; not valid for operational maneuver planning.",
    ]

    baseline_delta = abs(
        result.computed_baseline_miss_distance_km - req.baseline_miss_distance_km
    )
    if baseline_delta > max(0.05, 0.2 * req.baseline_miss_distance_km):
        warnings.append(
            "Provided baseline_miss_distance_km "
            f"({req.baseline_miss_distance_km:.3f} km) differs from this agent's "
            f"own propagated no-maneuver miss distance "
            f"({result.computed_baseline_miss_distance_km:.3f} km); "
            "verify upstream inputs."
        )

    return ManeuverResponse(
        event_id=req.event_id,
        baseline_miss_distance_km=req.baseline_miss_distance_km,
        target_miss_distance_km=req.target_miss_distance_km,
        candidates=ranked,
        recommended_candidate_id=recommended_id,
        warnings=warnings,
    )
