# Orbital Guardian — Module 10: Dashboard / Orchestration API

A working FastAPI orchestrator for the Orbital Guardian pipeline, plus a
mission-console dashboard. Every one of agents 1–9 is wired in behind a
single registry (`app/agents/registry.py`) with a reference/stub
implementation that honors the exact JSON contract from the build plan —
so the whole pipeline runs end-to-end today, and any teammate can drop in
their real agent without touching the orchestrator, the API, or the
frontend.

## Quick start

```bash
cd orbital_guardian
pip install -r requirements.txt
python run.py
# open http://localhost:8000
```

Click **Start Simulation**. Watch the Agent Activity panel light up as
tracking → prediction → collision detection → risk → maneuver → constraint
→ decision run live over SSE, and the risk/decision tables and orbit plot
populate as each stage finishes.

## What's real vs. stub right now

| Agent | Status | Notes |
|---|---|---|
| 1. Tracking | Real (synthetic mode) | Deterministic synthetic LEO fleet generator. `mode="celestrak"` raises `NotImplementedError` until someone wires up a real TLE fetch. |
| 2. Prediction | Simplified | Two-body Keplerian propagation, not SGP4. Real orbit shapes, no drag/J2. |
| 3. Collision Detection | Real | Vectorized brute-force closest-approach screening over the sampled ephemeris grid. Correct, not the filter-cascade/KD-tree performance story. |
| 4. Risk Assessment | Real | Numerically integrates the actual 2D Gaussian encounter-plane model (the integral Chan's/Foster's closed forms approximate). Pc values are genuine. |
| 5. Maneuver Planning | Simplified | Drift-rate rule (along-track) + CW oscillatory formula (radial/cross-track), rescaled by an inverse-square Pc heuristic. Trend is realistic; exact numbers are illustrative — see the docstring in `app/agents/maneuver.py` for exactly why and what a rigorous version needs. |
| 6. Mission Constraint | Real | Deterministic rule checks, as the build plan itself specifies this agent should be. |
| 7. Decision | Real | Simple "cheapest approved candidate under Pc threshold" rule, as specified. |
| 8. Negotiation | Real | Lowest-Δv-wins auction rule, as specified. |
| 9. Explanation | Stub (no LLM call) | Template-based report text + keyword-based constraint parsing. Same function signature an LLM-backed version would use — see docstring in `app/agents/explanation.py`. |

Run `GET /agent-status` to see this same table live from the running server.

## How to swap in a real agent

1. Write a module anywhere with a `run(...)` function matching the stub's
   signature (documented in each stub's docstring, and mirroring the build
   plan's JSON contract exactly).
2. In `app/agents/registry.py`, or from your own module at import time, call:
   ```python
   from app.agents.registry import register
   register("prediction", my_real_prediction_run, is_real=True)
   ```
3. Nothing else changes. `pipeline.py` and every route in `app/main.py`
   only ever call `registry.get("agent_name")` — never a stub module
   directly — so this is the only integration point that exists.

## API routes

All routes and their exact request/response shapes are documented in the
docstring at the top of `app/main.py`, and in depth (with rationale) in
`Orbital_Guardian_Module10_Dashboard_Orchestration_Implementation.md`.

Two additions beyond the original build-plan route list, both needed by the
dashboard and safe for any client to ignore:
- `GET /risks` and `GET /decisions` — bulk accessors (the build plan only
  specified single-pair/single-object GETs).
- `GET /catalog` and `GET /agent-status` — read-only conveniences.

## Project layout

```
orbital_guardian/
  requirements.txt
  run.py
  app/
    config.py          physical constants + operational thresholds
    schemas.py          Pydantic models, one per JSON contract
    main.py              FastAPI app + every route
    pipeline.py          the orchestration DAG (agent call order lives ONLY here)
    state/
      store.py           SQLite, one table per contract type
      events.py           pub/sub feeding the SSE agent-log stream
    agents/
      registry.py         real-vs-stub swap point
      orbital_math.py      shared Kepler/GMST helpers + honesty notes on their limits
      tracking.py, prediction.py, collision_detection.py, risk_assessment.py,
      maneuver.py, mission_constraint.py, decision.py, negotiation.py, explanation.py
  static/
    index.html, style.css, app.js     mission-console dashboard, no build step
```

## Testing

The agent stubs have no dependency on FastAPI/Pydantic and can be exercised
directly, which is the fastest way to sanity-check a change:

```python
from datetime import datetime, timezone
from app.agents import tracking, prediction, collision_detection, risk_assessment

epoch = datetime.now(timezone.utc)
objs = tracking.run(mode="synthetic", object_count=30, guaranteed_conjunctions=4, epoch_utc=epoch)
ephemerides = [prediction.run(o, window_hours=6, step_seconds=30) for o in objs]
conjunctions = collision_detection.run(ephemerides, None)["conjunctions"]
for c in conjunctions:
    print(risk_assessment.run({**c, "combined_hard_body_radius_m": 15.0}, epoch))
```

This exact snippet was used to validate the reference build before
shipping: with the default synthetic-fleet perturbation scale, it reliably
produces RED-tier conjunctions (Pc ~1e-3–1e-2 at sub-200m miss distances)
that the Maneuver → Constraint → Decision chain then resolves down to
Pc ~1e-6–1e-7 with sub-1 m/s along-track burns — the exact demo arc the
build plan describes.
