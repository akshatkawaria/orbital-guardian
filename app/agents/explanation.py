"""
Explanation / LLM Layer (build plan §9) -- reference/stub implementation.

Two entry points, matching the build plan's two input modes exactly:
  - generate_report(decision, risk) -> {"report_text": "..."}
  - parse_operator_command(text) -> {"constraints": {...}}

STUB NOTE: the build plan is explicit that this layer must "never compute
orbital mechanics itself" -- it only narrates numbers agents 1-7 already
produced, or extracts structured constraints from free text. This reference
build uses plain string templates and keyword matching for both, so the
whole pipeline is demoable with zero external API dependency. The natural
upgrade path (drop-in, same function signatures) is to call an LLM here --
e.g. the Anthropic Messages API with a system prompt that says exactly what
the build plan says: "turn this Decision Agent JSON into one paragraph" or
"extract {protected_ground_regions, optimize_for} from this operator
sentence as JSON" -- and parse its response into the same return shape.
Keeping that call behind this exact function signature means swapping in a
real LLM never touches pipeline.py or main.py.
"""
from __future__ import annotations
import re

_KNOWN_REGIONS = [
    "india", "usa", "united states", "china", "europe", "japan", "australia",
    "brazil", "russia", "canada", "africa", "middle east", "uk", "united kingdom",
]


def generate_report(decision: dict, risk: dict | None = None) -> dict:
    primary = decision["primary_id"]
    risk = risk or {}
    miss = risk.get("miss_distance_km")
    tmin = risk.get("time_to_tca_minutes")

    if decision["decision"] == "NO_ACTION":
        text = f"{primary} shows no significant collision risk at this time. No maneuver is recommended."
    elif decision["decision"] == "MONITOR":
        text = (
            f"{primary} has an elevated but sub-actionable collision risk"
            + (f", closing to within {miss:.2f} km" if miss is not None else "")
            + (f" in {tmin:.0f} minutes" if tmin is not None else "")
            + ". No approved maneuver currently clears the safety threshold without violating "
              "a mission constraint; continuing to monitor and re-evaluate as new tracking data arrives."
        )
    else:
        m = decision["selected_maneuver"]
        text = (
            f"{primary} has a high collision risk"
            + (f" with {risk.get('secondary_id', 'a nearby object')}" if risk.get("secondary_id") else "")
            + (f", closing to within {miss:.2f} km" if miss is not None else "")
            + (f" in {tmin:.0f} minutes. " if tmin is not None else ". ")
            + f"The recommended response is a {m['dv_ms']:.2f} m/s {m['direction'].replace('_', '-')} "
              f"burn at {m['burn_time_utc']}, which increases the miss distance to "
              f"{m['predicted_miss_km']:.1f} km and costs {m['fuel_cost_pct']:.1f}% of remaining fuel."
        )
    return {"report_text": text}


def parse_operator_command(text: str) -> dict:
    lowered = text.lower()
    constraints: dict = {}

    found_regions = [r for r in _KNOWN_REGIONS if r in lowered]
    if found_regions:
        constraints["protected_ground_regions"] = [r.title() for r in found_regions]

    if re.search(r"\bfuel\b", lowered) and re.search(r"\bminimi[sz]", lowered):
        constraints["optimize_for"] = "min_fuel"
    elif re.search(r"\btime\b", lowered) and re.search(r"\bminimi[sz]", lowered):
        constraints["optimize_for"] = "min_time"

    fuel_budget = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*fuel budget", lowered)
    if fuel_budget:
        constraints["max_fuel_budget_pct_remaining"] = float(fuel_budget.group(1))

    return {"constraints": constraints}


# `run` is the generic entry point the registry expects; it dispatches on
# which kind of input it was given, matching the build plan's "one agent,
# two input modes" framing (§9).
def run(mode: str, **kwargs) -> dict:
    if mode == "report":
        return generate_report(kwargs["decision"], kwargs.get("risk"))
    elif mode == "parse_command":
        return parse_operator_command(kwargs["text"])
    else:
        raise ValueError(f"Unknown explanation mode '{mode}'")
