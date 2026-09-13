#!/usr/bin/env python3
"""
Runs the Decision Agent against every fixture in fixtures/ and prints the
result. This is both a manual smoke test and a script you can run live in
front of judges to walk through each branch of the state machine on demand.

Usage:
    python3 run_fixtures.py
"""

import json
import glob
import os

from decision_agent import DecisionAgent


def main():
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    paths = sorted(glob.glob(os.path.join(fixtures_dir, "*.json")))

    agent = DecisionAgent()  # fresh agent per script run; no hysteresis carry-over between fixtures

    for path in paths:
        name = os.path.basename(path)
        with open(path) as f:
            raw_input = json.load(f)

        output = agent.decide(raw_input)

        print("=" * 78)
        print(f"FIXTURE: {name}")
        print("-" * 78)
        print(f"  primary_id / secondary_id : {output['primary_id']} / {output.get('secondary_id')}")
        print(f"  DECISION                  : {output['decision']}")
        print(f"  reason                    : {output['decision_reason']}")
        if output["selected_maneuver"]:
            m = output["selected_maneuver"]
            print(f"  selected maneuver         : {m['maneuver_id']}  dv={m['dv_ms']} m/s  "
                  f"meets_target={m['meets_pc_target']}")
            print(f"  confidence_pct            : {output['confidence_pct']}")
        if output["rejected_candidates"]:
            print("  rejected candidates:")
            for r in output["rejected_candidates"]:
                print(f"    - {r['maneuver_id']}: {r['violations']}")
        print()

    print("=" * 78)
    print(f"Ran {len(paths)} fixtures successfully.")


if __name__ == "__main__":
    main()
