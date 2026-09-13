# Decision Agent (Coordinator) — Orbital Guardian, Agent 7

A deterministic, dependency-free Python package implementing the Decision
Agent from the Orbital Guardian build plan. Takes Risk Assessment + Mission
Constraint Agent output, returns one clear action:
`NO_ACTION | MONITOR | MANEUVER_REQUIRED | ESCALATE_NO_VIABLE_OPTION`.

No orbital mechanics, no ML, no LLM calls — pure routing logic, per the
project's architectural rule that decision-making stays auditable.

## Install

```bash
pip install -e .    # editable install — the core package has zero external dependencies
```

If you also want to run `api_integration_example.py` (the FastAPI stub),
install those separately since they're only needed for that one file:
```bash
pip install fastapi uvicorn httpx
```

## Quickstart

```python
from decision_agent import DecisionAgent

agent = DecisionAgent()
output = agent.decide({
    "primary_id": "SAT-042",
    "secondary_id": "DEB-891",
    "risk_tier": "RED",
    "pc": 3.4e-3,
    "time_to_tca_minutes": 46,
    "pc_threshold_target": 1e-6,
    "evaluated_candidates": [
        {"maneuver_id": "M1", "approved": True,  "dv_ms": 0.72, "predicted_pc": 4.1e-7,
         "predicted_miss_km": 16.8, "fuel_cost_pct": 0.3, "violations": []},
        {"maneuver_id": "M2", "approved": False, "dv_ms": 0.91, "predicted_pc": 1.2e-7,
         "violations": ["altitude_band_exceeded"]},
    ],
})
print(output["decision"])           # "MANEUVER_REQUIRED"
print(output["selected_maneuver"])  # M1's full record + meets_pc_target flag
```

Run the demo script (walks through 6 fixture scenarios, prints each decision):
```bash
python3 run_fixtures.py
```

Run the tests:
```bash
python3 -m pytest decision_agent/tests/ -v
```

## Input schema

You have two upstream choices — see the "closing the gap" discussion below.
This package accepts **both** transparently.

**Preferred (`evaluated_candidates`) — gets you rejected-candidate visibility:**
```json
{
  "primary_id": "SAT-042",
  "secondary_id": "DEB-891",
  "risk_tier": "RED",
  "pc": 3.4e-3,
  "time_to_tca_minutes": 46,
  "pc_threshold_target": 1e-6,
  "evaluated_candidates": [
    { "maneuver_id": "M1", "approved": true,  "dv_ms": 0.72, "predicted_pc": 4.1e-7,
      "predicted_miss_km": 16.8, "fuel_cost_pct": 0.3, "burn_time_utc": "...",
      "direction": "along_track", "violations": [] },
    { "maneuver_id": "M2", "approved": false, "dv_ms": 0.91, "predicted_pc": 1.2e-7,
      "violations": ["altitude_band_exceeded"] }
  ]
}
```

**Build-plan literal shape (`approved_candidates`) — also accepted, back-compat:**
```json
{
  "primary_id": "SAT-042", "risk_tier": "RED", "pc": 3.4e-3,
  "approved_candidates": [
    { "maneuver_id": "M1", "dv_ms": 0.72, "predicted_pc": 4.1e-7, "fuel_cost_pct": 0.3 }
  ]
}
```
(`approved: true` is synthesized automatically for every item; no rejected
candidates are known in this shape, so `rejected_candidates` in the output
will be empty.)

Required fields: `primary_id`, `risk_tier` (`GREEN|YELLOW|ORANGE|RED`), `pc`.
Everything else has a sane default (`pc_threshold_target` defaults to `1e-6`,
matching CARA's convention of ~1.5 orders of magnitude below the 1e-4
high-risk line — see the research doc for the citation).

## Output schema

```json
{
  "primary_id": "SAT-042",
  "secondary_id": "DEB-891",
  "conjunction_id": "SAT-042_vs_DEB-891",
  "decision": "MANEUVER_REQUIRED",
  "decision_reason": "risk_tier=RED (pc=3.40e-03); selected M1 as lowest-Δv approved candidate meeting pc_threshold_target=1.0e-06.",
  "selected_maneuver": {
    "maneuver_id": "M1", "dv_ms": 0.72, "predicted_pc": 4.1e-7,
    "predicted_miss_km": 16.8, "fuel_cost_pct": 0.3, "meets_pc_target": true, "...": "..."
  },
  "rejected_candidates": [
    { "maneuver_id": "M2", "violations": ["altitude_band_exceeded"], "...": "..." }
  ],
  "confidence_pct": 64,
  "decided_at_utc": "2026-09-12T13:00:00Z"
}
```

`decision` is always one of: `NO_ACTION`, `MONITOR`, `MANEUVER_REQUIRED`,
`ESCALATE_NO_VIABLE_OPTION`. The last one is the edge case where risk is
real but Mission Constraint rejected every candidate — surfaced explicitly
rather than silently doing nothing.

## Design decisions worth knowing about (and why)

1. **Closing the input-contract gap.** The build plan's Mission Constraint
   Agent output includes rejected candidates + violation reasons; its
   Decision Agent input example shows only pre-filtered approved ones. This
   package accepts the fuller `evaluated_candidates` shape so rejected
   candidates flow through to `rejected_candidates` in the output — that's
   what makes the "M2 rejected: altitude_band_exceeded" dashboard story
   possible, which the build plan calls its most convincing demo beat.

2. **Selection rule is one `min()` call, on purpose.** Lowest `dv_ms` among
   approved candidates that clear `pc_threshold_target`, with a fully
   deterministic tiebreak (lower `predicted_pc`, then lower `fuel_cost_pct`,
   then `maneuver_id` lexical order). No weighted scoring function — legible
   over clever, per the build plan's own instruction.

3. **`ESCALATE_NO_VIABLE_OPTION` is a first-class decision, not an error.**
   RED/ORANGE risk with zero approved candidates is a real operational
   situation (every option blocked by fuel/altitude/secondary-conjunction
   constraints) — it gets its own decision value so the dashboard can show
   "human review required" instead of silently no-op'ing.

4. **Fallback to best-approved when nothing meets the target.** If no
   approved candidate actually clears `pc_threshold_target`, the agent picks
   the best available approved option and marks `meets_pc_target: false`
   rather than reporting a false "no option available." This matches real
   CA practice, where mitigation is sometimes iterative across cycles.

5. **Time-urgency bump applies to YELLOW/ORANGE only, never GREEN.** A
   conjunction with genuinely low Pc stays low-risk regardless of how little
   time is left — time pressure should not manufacture risk the probability
   model doesn't support. (Caught by `test_green_tier_not_bumped_even_if_tca_imminent`.)

6. **Hysteresis is opt-in and caller-driven, not hidden state.** The core
   rule functions in `rules.py` are pure (same input -> same output,
   always). `DecisionAgent.decide(..., track_state=True)` is a convenience
   wrapper for single-process demos; a real multi-worker deployment should
   persist `previous_decision` in the same state store the Tracking Agent
   uses and pass it explicitly via `previous_decision=`.

7. **`confidence_pct` is a legible composite, not a black box.** `0.7 *
   margin_below_pc_target + 0.3 * time_lead_factor`, both named constants.
   Not scientifically rigorous — reproducible and explainable, which is what
   the demo actually needs.

## Integration points

- **Upstream:** Risk Assessment Agent (`risk_tier`, `pc`, `time_to_tca_minutes`)
  + Mission Constraint Agent (`evaluated_candidates`). See
  `fixtures/*.json` for exact shapes to hand-write while those agents are
  still being built — this package needs nothing else to be testable today.
- **Downstream:** Negotiation Agent (only relevant if both objects in a
  conjunction are maneuverable satellites — feed it two Decision Agent
  outputs for the same `conjunction_id`), Dashboard (`decision`,
  `decision_reason`, before/after miss-distance numbers), Explanation/LLM
  layer (takes this JSON as input for natural-language report generation —
  never the reverse).
- **API:** see `api_integration_example.py` for a runnable FastAPI stub
  matching the build plan's `/maneuvers/{primary}`, `/decision/{primary}`,
  and `/agent-log` endpoints (§10).

## File layout

```
decision_agent/
├── pyproject.toml
├── README.md                      (this file)
├── run_fixtures.py                 demo/smoke-test script
├── api_integration_example.py      FastAPI wiring reference
├── fixtures/                       6 hand-written JSON scenarios
│   ├── 01_green_no_action.json
│   ├── 02_yellow_monitor.json
│   ├── 03_red_maneuver_required.json          <- the demo "money shot"
│   ├── 04_red_escalate_no_viable_option.json  <- the strong edge-case demo
│   ├── 05_orange_partial_mitigation_fallback.json
│   └── 06_yellow_time_urgency_bump.json
└── decision_agent/                 the actual package
    ├── __init__.py                 public API surface
    ├── models.py                   dataclasses + schema validation
    ├── rules.py                    pure decision-logic functions
    ├── decision_agent.py           orchestrator class
    └── tests/
        └── test_decision_agent.py  15 tests, one per behavior documented above
```
