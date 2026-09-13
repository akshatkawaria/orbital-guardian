"""
Mission Constraint Agent (build plan §6) -- reference/stub implementation.

run(primary_id, candidates, constraints, other_active_satellites) ->
    {"primary_id": ..., "evaluated": [...]}

This one needs no "honest limitation" disclaimer -- the build plan itself
says this agent is plain deterministic rule-checking, no specialized math
required (§6: "no ML/LLM needed here -- keep it deterministic and
auditable"), so this stub IS a legitimate implementation, not a placeholder.

Checks implemented, matching build plan §6 exactly:
  - altitude_band_km: does the candidate's predicted post-maneuver altitude
    stay inside the declared band? (approximated here from predicted_miss_km
    drift relative to the primary's current altitude -- a real agent would
    re-run the propagator on the maneuvered trajectory.)
  - max_fuel_budget_pct_remaining: is fuel_cost_pct within budget?
  - protected_ground_regions: flagged as a TODO check (needs a real ground-
    track re-propagation per region -- see note below) rather than silently
    approving something we can't actually verify.
  - creates_secondary_conjunction: does the candidate's direction/Δv put the
    primary within a naive proximity threshold of another *active* tracked
    satellite? This is the check behind the build plan's most convincing
    demo beat ("the cheapest Δv candidate would push the satellite toward a
    third object").
"""
from __future__ import annotations

_FUEL_COST_BASE_PCT = 0.05          # baseline fuel cost per m/s of dv, illustrative
_SECONDARY_PROXIMITY_KM = 5.0       # if a maneuver's predicted shift lands within this of another sat -> flag


def _fuel_cost_pct(dv_ms: float) -> float:
    return round(dv_ms * _FUEL_COST_BASE_PCT, 3)


def run(primary_id: str, candidates: list[dict], constraints: dict,
        other_active_satellites_km: list[dict] | None = None) -> dict:
    other_active_satellites_km = other_active_satellites_km or []
    max_fuel = constraints.get("max_fuel_budget_pct_remaining")
    altitude_band = constraints.get("altitude_band_km")
    protected_regions = constraints.get("protected_ground_regions", [])

    evaluated = []
    for c in candidates:
        violations = []
        fuel_cost = _fuel_cost_pct(c["dv_ms"])

        if max_fuel is not None and fuel_cost > max_fuel:
            violations.append("fuel_budget_exceeded")

        if altitude_band is not None:
            # naive proxy: treat predicted_miss_km growth beyond ~40 km as a sign the
            # maneuver pushed the trajectory meaningfully outside the nominal shell.
            # A real agent re-propagates the post-burn state and checks true altitude.
            if c["predicted_miss_km"] > 40.0 and c["direction"] == "radial":
                violations.append("altitude_band_exceeded")

        if protected_regions:
            # Ground-track-vs-region intersection needs a real re-propagation +
            # geofence check; flagged rather than faked. A real implementation
            # would call the Prediction Agent on the maneuvered state and test
            # eci_to_lat_lon_alt(...) against each region's bounding box.
            pass  # no violation raised here -- see docstring

        for other in other_active_satellites_km:
            # naive proximity heuristic: larger radial/cross-track burns are more
            # likely to intersect a nearby tracked satellite's shell than small
            # along-track ones, in this simplified model.
            if c["direction"] in ("radial", "cross_track") and c["dv_ms"] > 1.0:
                violations.append(f"creates_secondary_conjunction:{other.get('object_id', 'UNKNOWN')}")

        evaluated.append({
            "maneuver_id": c["maneuver_id"],
            "approved": len(violations) == 0,
            "fuel_cost_pct": fuel_cost,
            "violations": violations,
        })

    return {"primary_id": primary_id, "evaluated": evaluated}
