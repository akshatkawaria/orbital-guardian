"""
Negotiation Agent (build plan §8, optional stretch) -- reference/stub implementation.

run(conjunction_id, proposals) -> {"conjunction_id", "mover", "rationale", "dv_ms"}

Matches the build plan's own framing exactly (§8: "a simple auction/lowest-
cost rule is defensible and honestly matches the current state of the art
... there is no deployed automated inter-operator negotiation protocol yet").
Lower Δv wins; lower fuel_remaining_pct is the tiebreak penalty (an operator
with less fuel left is a worse candidate to ask to move).
"""
from __future__ import annotations


def run(conjunction_id: str, proposals: list[dict]) -> dict:
    if len(proposals) < 2:
        raise ValueError("Negotiation requires at least two proposals (two-satellite case)")

    def score(p: dict) -> tuple[float, float]:
        # primary sort: lower dv wins. tiebreak: prefer moving whoever has MORE fuel
        # remaining (i.e. penalize low fuel_remaining_pct by inverting it for sort).
        return (p["min_dv_ms_to_clear"], -p["fuel_remaining_pct"])

    winner = min(proposals, key=score)
    rationale = "lower_dv_required"
    if all(abs(p["min_dv_ms_to_clear"] - winner["min_dv_ms_to_clear"]) < 1e-6 for p in proposals):
        rationale = "tiebreak_higher_fuel_remaining"

    return {
        "conjunction_id": conjunction_id,
        "mover": winner["object_id"],
        "rationale": rationale,
        "dv_ms": winner["min_dv_ms_to_clear"],
    }
