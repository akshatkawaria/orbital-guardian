"""
Standalone demo runner — no other Orbital Guardian agent needs to exist for
this to run. This is the "watch it reject the maneuver that would hit
SAT-107" beat from the build plan's phase-6 build-order table.

    python3 -m mission_constraint_agent.run_demo
"""

from __future__ import annotations

import json
from pathlib import Path

from .agent import evaluate_candidates
from .config import MissionConstraintConfig
from .state_provider import InMemoryStateProvider

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "demo_scenario.json"


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())

    provider = InMemoryStateProvider()
    ps = fixture["primary_state"]
    provider.register(ps["object_id"], ps["r_km"], ps["v_kms"], ps["mean_motion_rad_s"])

    print("Mission Constraint Agent — demo scenario")
    print("=" * 60)
    print(f"Primary: {fixture['request']['primary_id']}")
    print(f"Candidates from Maneuver Agent: "
          f"{[c['maneuver_id'] for c in fixture['request']['candidates']]}")
    print(f"Constraints: {json.dumps(fixture['request']['constraints'], indent=2)}")
    print()

    result = evaluate_candidates(
        fixture["request"],
        state_provider=provider,
        config=MissionConstraintConfig.demo_scale(),
    )

    print("Evaluation results:")
    print("-" * 60)
    for row in result["evaluated"]:
        status = "APPROVED" if row["approved"] else "REJECTED"
        print(f"  {row['maneuver_id']}: {status}  (fuel {row['fuel_cost_pct']}%)")
        for v in row["violations"]:
            print(f"      x violation: {v}")
    print()

    approved = [r for r in result["evaluated"] if r["approved"]]
    if approved:
        print(f">> Cleared for the Decision Agent: {[r['maneuver_id'] for r in approved]}")
    else:
        print(">> No candidate cleared all constraints — Decision Agent should re-request "
              "candidates from the Maneuver Agent or escalate to an operator.")


if __name__ == "__main__":
    main()
