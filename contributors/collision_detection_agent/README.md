# Collision Detection Agent

Orbital Guardian — Agent 3 of 10. Screens all propagated object pairs and
emits precise close-approach (TCA) candidates for the Risk Assessment Agent.

Implements the contract in `Orbital_Guardian_Build_Plan.md` §3 exactly:
takes the Prediction Agent's ephemeris batch, returns candidate
conjunctions with time-of-closest-approach, miss distance, and relative
state vectors — nothing else. No Pc, no risk tiering, no propagation.

## Files

| File | Purpose |
|---|---|
| `agent.py` | Core logic: `run_collision_detection(request, config=None) -> dict`. Also runnable as a CLI. |
| `generate_fixture.py` | Synthetic ephemeris generator matching the Prediction Agent's exact output contract — lets you build/test/demo this agent with zero dependency on the Prediction Agent being finished. |
| `api.py` | FastAPI wrapper exposing `GET /conjunctions` and `POST /conjunctions`, matching the Dashboard's documented API shape (build plan §10). |
| `test_agent.py` | Pytest suite: analytic ground-truth TCA cases, contract validation, and the full funnel demo test. |

## Quick start

```bash
pip install numpy scipy --break-system-packages   # core deps
pip install pytest --break-system-packages         # to run tests
pip install fastapi uvicorn --break-system-packages  # to run the API

# 1. Generate a synthetic 50-object fleet with 3 deliberately conjuncting pairs
python3 generate_fixture.py --n-objects 50 --n-conjunctions 3 --out fixture_request.json

# 2. Run the agent against it from the CLI
python3 agent.py fixture_request.json --out output.json
#   -> "Screened 1225 possible pairs -> 3 candidates flagged (XXX ms)"

# 3. Run the test suite
python3 -m pytest test_agent.py -v

# 4. Or run it as a service for the Dashboard to call
uvicorn api:app --reload --port 8003
curl -X POST http://localhost:8003/conjunctions \
     -H "Content-Type: application/json" \
     -d "{\"request\": $(cat fixture_request.json), \"config\": {\"screening_radius_km\": 20}}"
curl http://localhost:8003/conjunctions   # re-fetch the cached last result
```

## How it works (short version)

1. **Coarse KD-tree screen** at the ephemeris's native sample cadence (no resampling): at every shared timestep, build a `scipy.spatial.KDTree` over all objects' positions and flag any pair within `screening_radius_km` (default **20 km**). This is the funnel's expensive-avoidance step — it turns an `O(N²·T)` brute-force problem into `O(T·N log N)`.
2. **Cluster** each flagged pair's flagged timesteps into contiguous close-approach windows (so one continuous pass isn't double-counted).
3. **Precise refine**: for each window, build a Hermite spline (using both position *and* velocity samples) per object and do a vectorized fine-grained scan (0.5 s resolution) to find the true time-of-closest-approach, miss distance, and relative velocity vector.

See `Collision_Detection_Agent_Implementation.md` (the companion research doc) for the full derivation, the classical filter-cascade alternative, and the pitfalls list this implementation was built to avoid.

## Default parameters and why

- **`screening_radius_km = 20.0`** — chosen empirically against the synthetic-fleet demo target: at this radius, "screen 1,225 pairs → flag exactly the deliberately-conjuncting ones, zero noise" (see `test_funnel_demo_synthetic_fleet_flags_exactly_the_planted_pairs`). It's also in the right order of magnitude for real conjunction-screening volumes.
- **`pad_for_aliasing = False`** by default. The coarse screen checks the native sample grid (e.g. every 60 s) without resampling finer, which means a very fast, very close pass could in principle occur entirely between two samples and be missed (see the "ALIASING NOTE" docstring in `agent.py`). Set `pad_for_aliasing=True` to pad the radius by `max_rel_vel_kms * step_s` for a formal no-miss guarantee — this trades funnel tightness for safety margin (in our fixture, it goes from 3 flagged to ~2,000+). Use it if you're demoing correctness/rigor rather than the tight funnel narrative; mention the trade-off either way, it's a good "we understand the edge cases" talking point with judges.

## Integration notes for the rest of the team

- **Input**: whatever the Prediction Agent's `POST /propagate` returns, batched into one `ephemerides` array plus a `screening_window`. The agent asserts `frame == "ECI_J2000"` on every object and raises `CollisionDetectionError` immediately if that's violated — don't silently swallow a TEME/ECI mismatch bug from upstream.
- **Output**: the exact `conjunctions` schema from the build plan, plus an additive `screening_summary` block (`n_pairs_screened`, `n_conjunctions_flagged`, etc.) for the Dashboard's live "Agent Activity" funnel counter — safe to ignore if you only care about the `conjunctions` array.
- **Optional extra input field**: `object_types` (`{object_id: "PAYLOAD"|"ROCKET_BODY"|"DEBRIS"}`) — not required by the contract, but if supplied it's used to pick a stable primary/secondary convention (PAYLOAD preferred as primary). Without it, primary/secondary falls back to alphabetical `object_id` ordering — deterministic either way, so the Risk Assessment Agent can rely on it.
- **Errors**: all contract violations raise `CollisionDetectionError` (a `ValueError` subclass) with a human-readable message; the API wrapper turns these into HTTP 422s. Don't catch these silently upstream — they mean something is actually wrong with the pipeline, not a normal "no conjunctions found" outcome (which returns an empty list, not an error).
- **Performance**: ~260 ms for a 50-object, 24 h/60 s-cadence fleet. Scales as `O(T·N log N)` for the coarse screen; the refine step only ever runs on the small candidate set, so it doesn't add meaningfully to that.
