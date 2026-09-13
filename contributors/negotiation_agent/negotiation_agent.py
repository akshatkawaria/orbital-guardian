"""
Negotiation Agent (Orbital Guardian, Module 8)

Deterministic, auditable rule for deciding which of two maneuverable
satellites should move to resolve a shared conjunction, given each
side's independently-computed Decision Agent output.

No ML, no LLM — every decision must be explainable in one sentence.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DV_EPSILON_MS = 0.02  # Δv differences at or below this are treated as a tie


class UnresolvableConjunction(Exception):
    """Raised when neither satellite reports a valid maneuver option."""


@dataclass
class Proposal:
    object_id: str
    min_dv_ms_to_clear: Optional[float]
    fuel_remaining_pct: Optional[float]


def negotiate(conjunction_id: str, proposals: list[Proposal]) -> dict:
    """
    Decide which satellite moves for a single pairwise conjunction.

    Rule:
      1. If only one side has a valid dv -> that side moves ("only_one_capable").
      2. Else the side with the LOWER min_dv_ms_to_clear moves ("lower_dv_required").
      3. If the two dv values are within DV_EPSILON_MS of each other, treat as a
         tie and let the side with MORE fuel_remaining_pct move instead
         ("tiebreak_lower_fuel_reserve") -- it can better absorb the cost.
      4. If dv AND fuel are both exactly tied, fall back to lexicographic
         object_id order so the function is always deterministic and never
         raises on valid input.
      5. If neither side has a valid dv, raise UnresolvableConjunction --
         this must surface as an explicit "escalate to human operator"
         event in the agent log, never a silent failure.
    """
    if len(proposals) != 2:
        raise ValueError("Negotiation Agent v1 only supports pairwise conjunctions (exactly 2 proposals)")

    a, b = proposals

    valid_a = a.min_dv_ms_to_clear is not None
    valid_b = b.min_dv_ms_to_clear is not None

    if valid_a and not valid_b:
        mover, runner_up, rationale = a, b, "only_one_capable"
    elif valid_b and not valid_a:
        mover, runner_up, rationale = b, a, "only_one_capable"
    elif not valid_a and not valid_b:
        raise UnresolvableConjunction(
            f"Neither satellite in {conjunction_id} has a valid maneuver option; "
            "escalate to human operator."
        )
    else:
        diff = abs(a.min_dv_ms_to_clear - b.min_dv_ms_to_clear)
        if diff <= DV_EPSILON_MS:
            if a.fuel_remaining_pct == b.fuel_remaining_pct:
                mover, runner_up = sorted([a, b], key=lambda p: p.object_id)
                rationale = "tiebreak_lower_fuel_reserve"
            elif a.fuel_remaining_pct > b.fuel_remaining_pct:
                mover, runner_up, rationale = a, b, "tiebreak_lower_fuel_reserve"
            else:
                mover, runner_up, rationale = b, a, "tiebreak_lower_fuel_reserve"
        elif a.min_dv_ms_to_clear < b.min_dv_ms_to_clear:
            mover, runner_up, rationale = a, b, "lower_dv_required"
        else:
            mover, runner_up, rationale = b, a, "lower_dv_required"

    return {
        "conjunction_id": conjunction_id,
        "mover": mover.object_id,
        "rationale": rationale,
        "dv_ms": mover.min_dv_ms_to_clear,
        "runner_up": {
            "object_id": runner_up.object_id,
            "dv_ms": runner_up.min_dv_ms_to_clear,
        },
        "decided_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def negotiate_from_dict(payload: dict) -> dict:
    """Convenience wrapper: accepts the raw JSON contract straight from
    the build plan and returns the raw JSON output contract."""
    proposals = [
        Proposal(
            object_id=p["object_id"],
            min_dv_ms_to_clear=p.get("min_dv_ms_to_clear"),
            fuel_remaining_pct=p.get("fuel_remaining_pct"),
        )
        for p in payload["proposals"]
    ]
    return negotiate(payload["conjunction_id"], proposals)


if __name__ == "__main__":
    # Quick manual smoke test matching the build-plan's headline example
    example = {
        "conjunction_id": "SAT-042_vs_SAT-107",
        "proposals": [
            {"object_id": "SAT-042", "min_dv_ms_to_clear": 0.7, "fuel_remaining_pct": 62},
            {"object_id": "SAT-107", "min_dv_ms_to_clear": 0.4, "fuel_remaining_pct": 18},
        ],
    }
    import json
    print(json.dumps(negotiate_from_dict(example), indent=2))
