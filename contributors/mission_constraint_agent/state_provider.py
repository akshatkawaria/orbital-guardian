"""
The Mission Constraint Agent needs the primary object's current state
(r_km, v_kms, mean_motion_rad_s) to run its checks, but per the build plan's
JSON-contract-per-arrow design, that data properly belongs to the Tracking
Agent, not to this agent's own input payload.

StateProvider is the seam: today it's backed by an in-memory dict (for unit
tests and the standalone demo). When the Tracking Agent is ready, swap in
TrackingAgentStateProvider (or any object implementing the same two
methods) with zero changes to agent.py or checks.py.
"""

from __future__ import annotations

from typing import Protocol, TypedDict


class PrimaryState(TypedDict):
    r_km: tuple[float, float, float]
    v_kms: tuple[float, float, float]
    mean_motion_rad_s: float


class StateProvider(Protocol):
    def get_primary_state(self, object_id: str) -> PrimaryState: ...


class InMemoryStateProvider:
    """Demo/test implementation — register states by hand or load a fixture."""

    def __init__(self, states: dict[str, PrimaryState] | None = None):
        self._states = dict(states or {})

    def register(self, object_id: str, r_km, v_kms, mean_motion_rad_s: float) -> None:
        self._states[object_id] = {
            "r_km": tuple(r_km),
            "v_kms": tuple(v_kms),
            "mean_motion_rad_s": mean_motion_rad_s,
        }

    def get_primary_state(self, object_id: str) -> PrimaryState:
        try:
            return self._states[object_id]
        except KeyError as exc:
            raise LookupError(
                f"No current state registered for '{object_id}'. "
                "Wire this StateProvider up to the Tracking Agent's store, "
                "or register a fixture state for local testing."
            ) from exc


class TrackingAgentStateProvider:
    """
    Production implementation, to be filled in once the Tracking Agent
    exposes a lookup (its own catalog table, or a shared service call).
    Left as a documented stub so integration is a one-file change.
    """

    def __init__(self, tracking_agent_client):
        self._client = tracking_agent_client

    def get_primary_state(self, object_id: str) -> PrimaryState:
        # Example shape once wired up:
        #   record = self._client.get_current_state(object_id)
        #   return {
        #       "r_km": record["r_km"],
        #       "v_kms": record["v_kms"],
        #       "mean_motion_rad_s": record["mean_motion_rad_s"],
        #   }
        raise NotImplementedError(
            "Wire this up to the real Tracking Agent client once it's available."
        )
