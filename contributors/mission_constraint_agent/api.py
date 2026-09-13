"""
FastAPI router for the Mission Constraint Agent.

Per Orbital_Guardian_Build_Plan.md §10, this agent doesn't own a top-level
route — it's chained inside POST /maneuvers/{primary}
(Maneuver Agent -> Mission Constraint Agent -> Decision Agent). This module
exposes both:

  1. A standalone POST /constraints/evaluate endpoint — lets the Mission
     Constraint Agent be built, demoed, and curl-tested in complete
     isolation before the rest of the pipeline exists (matches the build
     plan's "any agent can be swapped for a stub" design rule).
  2. `mount_in_pipeline(...)`, a plain function (not a route) that the
     orchestration API's /maneuvers/{primary} handler calls directly once
     it's wiring the full chain together.

Mount the router in the main app with:
    app.include_router(mission_constraint_router)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .agent import evaluate_candidates
from .config import MissionConstraintConfig
from .models import MissionConstraintRequest, MissionConstraintResponse
from .state_provider import StateProvider

mission_constraint_router = APIRouter(prefix="/constraints", tags=["mission-constraint-agent"])

# Swap this for a TrackingAgentStateProvider once the Tracking Agent exists —
# every other file in this package is unaffected by that swap.
_default_state_provider: StateProvider | None = None


def set_state_provider(provider: StateProvider) -> None:
    global _default_state_provider
    _default_state_provider = provider


@mission_constraint_router.post("/evaluate", response_model=MissionConstraintResponse)
def evaluate_endpoint(request: MissionConstraintRequest) -> dict:
    if _default_state_provider is None:
        raise HTTPException(
            status_code=503,
            detail="No state provider configured — call set_state_provider() at app startup.",
        )
    return evaluate_candidates(request.model_dump(), state_provider=_default_state_provider)


def mount_in_pipeline(
    maneuver_agent_output: dict,
    constraints: dict,
    state_provider: StateProvider,
    config: MissionConstraintConfig | None = None,
) -> dict:
    """
    Convenience function for the /maneuvers/{primary} orchestration handler:
    combines the Maneuver Agent's raw output with the operator's declared
    constraints into this agent's input shape, evaluates, and returns the
    dict ready to pass straight to the Decision Agent.
    """
    payload = {
        "primary_id": maneuver_agent_output["primary_id"],
        "candidates": maneuver_agent_output["candidates"],
        "constraints": constraints,
    }
    return evaluate_candidates(payload, state_provider=state_provider, config=config)
