"""
Mission Constraint Agent — main entrypoint.

Usage by other agents / the orchestration API:

    from mission_constraint_agent.agent import evaluate_candidates
    from mission_constraint_agent.state_provider import InMemoryStateProvider

    provider = InMemoryStateProvider()
    provider.register("SAT-042", r_km=[...], v_kms=[...], mean_motion_rad_s=0.00113)

    result = evaluate_candidates(maneuver_agent_output_dict, state_provider=provider)
    # result is a plain dict matching Orbital_Guardian_Build_Plan.md §6's output schema,
    # ready to hand to the Decision Agent unchanged.

No network calls, no randomness, no LLM calls — safe to call synchronously
inline in the FastAPI request handler that chains Maneuver -> Mission
Constraint -> Decision (build plan §10, POST /maneuvers/{primary}).
"""

from __future__ import annotations

from .checks import check_altitude_band, check_fuel, check_ground_region, check_secondary_conjunctions
from .config import MissionConstraintConfig
from .models import EvaluatedCandidate, MissionConstraintRequest, MissionConstraintResponse
from .state_provider import StateProvider


def evaluate_candidates(
    payload: dict,
    state_provider: StateProvider,
    config: MissionConstraintConfig | None = None,
) -> dict:
    """
    payload: dict matching build plan §6's input schema exactly
             ({"primary_id", "candidates", "constraints"})
    state_provider: anything implementing get_primary_state(object_id) ->
             {"r_km", "v_kms", "mean_motion_rad_s"} — see state_provider.py
    config: optional threshold overrides; defaults from config.py apply otherwise

    Returns a plain dict matching build plan §6's output schema exactly.
    """
    request = MissionConstraintRequest.model_validate(payload)
    cfg = config or MissionConstraintConfig()
    primary_state = state_provider.get_primary_state(request.primary_id)

    evaluated: list[EvaluatedCandidate] = []
    for candidate in request.candidates:
        violations: list[str] = []

        fuel_violation = check_fuel(candidate, request.constraints)
        if fuel_violation:
            violations.append(fuel_violation)

        altitude_violation = check_altitude_band(candidate, primary_state, request.constraints)
        if altitude_violation:
            violations.append(altitude_violation)

        ground_violation = check_ground_region(candidate, request.constraints, cfg)
        if ground_violation:
            violations.append(ground_violation)

        violations.extend(
            check_secondary_conjunctions(candidate, primary_state, request.constraints, cfg)
        )

        evaluated.append(
            EvaluatedCandidate(
                maneuver_id=candidate.maneuver_id,
                approved=len(violations) == 0,
                fuel_cost_pct=candidate.fuel_cost_pct if candidate.fuel_cost_pct is not None else 0.0,
                violations=violations,
            )
        )

    response = MissionConstraintResponse(primary_id=request.primary_id, evaluated=evaluated)
    return response.model_dump()
