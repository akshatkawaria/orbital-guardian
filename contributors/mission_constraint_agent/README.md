# Mission Constraint Agent

Orbital Guardian, Agent 6. Deterministic, auditable, no ML/LLM — takes the
Maneuver Agent's Δv candidates and the operator's declared constraints, and
returns which candidates are safe to hand to the Decision Agent.

Implements the contract in `Orbital_Guardian_Build_Plan.md` §6, exactly.
See `Mission_Constraint_Agent_Implementation.md` for the full design
rationale behind each check.

```
pip install -r requirements.txt
python3 -m pytest tests/ -v      # 18 tests, all green
python3 -m mission_constraint_agent.run_demo   # standalone demo, no other agent needed
```

---

## What's in this package

| File | Purpose |
|---|---|
| `agent.py` | `evaluate_candidates(payload, state_provider, config)` — the one function other agents call |
| `checks.py` | The four constraint checks, each pure and independently testable |
| `orbital_math.py` | Closed-form Δa estimate, CW relative-motion propagation, drift heuristic — all deterministic |
| `models.py` | Pydantic models = the JSON contract, enforced |
| `config.py` | Every tunable threshold, with reasoning; includes `MissionConstraintConfig.demo_scale()` |
| `state_provider.py` | Pluggable seam for the primary object's current state — in-memory now, Tracking Agent later |
| `api.py` | FastAPI router; standalone `/constraints/evaluate` route + `mount_in_pipeline()` for the orchestrator |
| `fixtures/` | JSON fixtures other teammates can run against without this agent's code |
| `tests/` | 18 pytest tests: per-check unit tests + fixture-driven integration tests |
| `run_demo.py` | The phase-6 demo beat, runnable standalone |

---

## How another agent calls this one

### Directly, as a library (recommended inside the orchestrator)

```python
from mission_constraint_agent import evaluate_candidates, InMemoryStateProvider

provider = InMemoryStateProvider()
provider.register("SAT-042", r_km=[...], v_kms=[...], mean_motion_rad_s=0.00113)

maneuver_agent_output = {  # whatever the Maneuver Agent returned
    "primary_id": "SAT-042",
    "candidates": [...],
}
operator_constraints = {  # from the Explanation/LLM layer or a static config
    "altitude_band_km": [548, 552],
    "protected_ground_regions": ["India"],
    "max_fuel_budget_pct_remaining": 0.5,
    "other_active_satellites_km": [...],
}

payload = {**maneuver_agent_output, "constraints": operator_constraints}
result = evaluate_candidates(payload, state_provider=provider)
# result: {"primary_id": ..., "evaluated": [{"maneuver_id", "approved", "fuel_cost_pct", "violations"}, ...]}
# hand `result` straight to the Decision Agent — same shape build_plan §7 expects as input.
```

### Over HTTP, once the orchestrator's FastAPI app exists

```python
# in the main app:
from mission_constraint_agent.api import mission_constraint_router, set_state_provider
app.include_router(mission_constraint_router)
set_state_provider(my_tracking_agent_state_provider)
```
```
POST /constraints/evaluate
Content-Type: application/json
{ "primary_id": "SAT-042", "candidates": [...], "constraints": {...} }
```

### Chained inside `/maneuvers/{primary}` (build plan §10's intended shape)

```python
from mission_constraint_agent.api import mount_in_pipeline

evaluated = mount_in_pipeline(
    maneuver_agent_output=maneuver_agent.plan(primary_id),
    constraints=get_operator_constraints(primary_id),
    state_provider=tracking_agent_state_provider,
)
decision = decision_agent.decide(risk_assessment_output, evaluated)
```

---

## Wiring in the real Tracking Agent

Right now `InMemoryStateProvider` is used everywhere (tests, demo, fixtures).
Once the Tracking Agent exists, implement `TrackingAgentStateProvider.get_primary_state()`
in `state_provider.py` (the stub and its docstring show the expected shape) and swap it
in wherever a provider is constructed. **No other file in this package changes.**
That's the point of the seam — it's exactly the "swap for a stub" design rule
from the build plan's architecture section.

---

## How the demo fixture was built

`fixtures/demo_scenario.json` is a **physically self-consistent** stand-in
for the build plan's §6 worked example — not a byte-for-byte copy of it.
The build plan's own numbers (an SAT-042 position vector paired with a
540–560 km altitude band) don't actually correspond to the same orbit when
you check the math (that r-vector's magnitude implies ~1080 km altitude,
not ~550 km) — expected, since the build plan's JSON snippets are
illustrative contracts, not a single coherent physics scenario.

This fixture fixes that: a real circular 550 km LEO orbit
(`a = 6378.137 + 550 = 6928.137 km`, mean motion computed exactly from
`n = sqrt(mu / a^3)`), with SAT-107's position numerically derived (via a
grid search over the Clohessy-Wiltshire relative-offset field — see the
build transcript / implementation-plan §2.4) so that **only** the radial
burn's projected trajectory passes within the secondary-conjunction
screening threshold over the 2-hour screening window, while both
along-track burns stay clear of it. That reproduces the build plan's exact
demo target (M1 approved, M2 altitude-rejected, M3 double-rejected)
starting from numbers that actually satisfy Newton's laws.

Run `python3 -m mission_constraint_agent.run_demo` to see it end to end.

**One scale caveat worth knowing (see `config.py` for the full note):**
the build plan's own candidate `dv_ms` values (~0.7–1.2 m/s) are on the
"illustrative" scale the Research Reference explicitly warns is ~50–100x
larger than a realistic operational avoidance burn (single-digit mm/s to a
few cm/s). At the illustrative scale, the ground-track-drift heuristic
would flag even M1/M2 as an artifact of that scale mismatch — so the demo
fixture runs under `MissionConstraintConfig.demo_scale()`, which widens
just the ground-region tolerance to match. Once the Maneuver Agent is
producing realistic-scale Δv values, use the default config everywhere.

---

## Known simplifications (state these to judges up front, not defensively)

- **Altitude-band check** is a first-order `Δa ≈ 2Δv/n` estimate, not a
  full re-propagation. Good for ranking/filtering candidates cheaply;
  re-confirm the Decision Agent's final pick with full SGP4 before executing.
- **Ground-region check** is a Tier-1 linear heuristic that only models
  along-track drift; radial/cross-track burns score 0 under it by
  construction (documented blind spot, not a false "all clear" — route
  those to a full re-propagation check manually near protected regions).
- **Secondary-conjunction check** treats the other object as stationary in
  the primary's original LVLH frame over the screening window (no
  ephemeris for the secondary is available in this agent's input contract).
  Fine for a fast screening pass whose job is to flag "worth a Risk
  Assessment re-check" — not fine as a final collision certification.

All three are exactly the kind of "fast filter, expensive confirm only for
the finalist" tradeoff the rest of the pipeline already makes (SGP4 vs.
CW equations, filter cascade vs. brute force) — consistent with the
project's overall design philosophy, not a shortcut unique to this agent.
