# Orbital Guardian — Agent 5: Maneuver Agent

Generates and ranks candidate satellite collision-avoidance maneuvers from a
predicted close-approach event, using a simplified, documented two-body
orbital model.

**This is a hackathon simulation. It does not control, command, or provide
operational guidance for any real spacecraft.**

Agent 5 sits between Agent 4 (Risk Assessment) and Agent 6 (Mission
Constraints) in the Orbital Guardian pipeline: it only *proposes and ranks*
maneuvers. It never decides which one is executed — that's Agent 6 (fuel/
mission checks) and Agent 7 (final decision).

## Install

```bash
cd maneuver_agent
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

## Endpoints

- `GET /health` — liveness check.
- `POST /maneuvers/generate` — generate and rank maneuver candidates for a
  close-approach event. See `sample_request.json` for the expected shape.

### Example

```bash
curl -X POST http://localhost:8000/maneuvers/generate \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

## Tests

```bash
pytest -v
```

27 tests covering the orbital frame, the propagator (energy/angular-momentum
conservation, circular-orbit period sanity checks), candidate generation and
ranking, and the API (including validation/422 cases and a determinism
check that two identical calls return byte-identical responses).

## Model summary

- Units: kilometres, seconds, km/s throughout (internally); delta-v is
  accepted in m/s in the request, matching upstream/CDM convention.
- **Local orbital frame (RTN):** at burn time, `R̂` = radial (away from
  Earth), `N̂` = orbit normal (`r × v` direction), `T̂` = transverse
  (`N̂ × R̂`, in-plane, in the direction of motion). Burns are applied along
  `±R̂` (`radial_out`/`radial_in`), `±T̂` (`prograde`/`retrograde`), and `±N̂`
  (`normal`/`anti_normal`).
- **Propagation:** unperturbed two-body numerical integration
  (`scipy.integrate.solve_ivp`, `DOP853`, tight fixed tolerances — no
  randomness, fully deterministic) of `d²r/dt² = -μr/|r|³` with
  `μ_Earth = 398600.4418 km³/s²`. The satellite is propagated
  reference→burn time unperturbed, the requested Δv is added
  instantaneously at burn time, then propagated burn→encounter. Debris is
  propagated reference→encounter directly (no burn). Miss distance is the
  Euclidean norm of the position difference at the predicted
  time-of-closest-approach.
- **Candidate generation:** one candidate per `(direction, Δv)` pair in the
  Cartesian product of `allowed_directions` × `delta_v_options_m_s`.
- **Ranking (deterministic, no randomness):**
  1. Candidates meeting `target_miss_distance_km` rank ahead of ones that don't.
  2. Among those meeting target: lowest Δv first; ties broken by larger miss distance.
  3. If none meet target: ranked by largest improvement over baseline.
  4. All candidates are always returned (Agents 6/7 need the full set).

## Assumptions & limitations (please read before demoing)

- **No perturbations:** no J2/oblateness, atmospheric drag, or luni-solar
  gravity. Valid for the short (minutes-to-a-few-hours) windows this demo
  targets; not valid for multi-day predictions.
- **Impulsive burns:** delta-v is added instantaneously rather than modeled
  as a finite-duration thrust arc. Standard, negligible-error approximation
  for the small (cm/s–dm/s) burns used here.
- **RTN transverse ≈ prograde:** `T̂` (from `N̂ × R̂`) exactly equals the
  velocity direction only for a perfectly circular orbit. For the
  near-circular LEO case this demo targets the two are very close; for a
  markedly eccentric orbit they would diverge and a velocity-aligned frame
  would be a better choice of "prograde."
- **Two-body only, no covariance:** this agent does not propagate
  position/velocity uncertainty — that's Agent 4's (Risk Assessment) job.
  It also does not check fuel budgets, other conjunctions the maneuver
  might create, or altitude-band mission constraints — that's Agent 6.
- **Baseline self-check:** the agent independently propagates the
  no-maneuver case and compares it against the caller-supplied
  `baseline_miss_distance_km`. If they disagree beyond a small tolerance, a
  warning is added to the response rather than silently trusting either
  number — see "Example calculated response" below, where this actually
  fires on the brief's own example payload.

## Example calculated response

Running the exact example payload from the project brief
(`sample_request.json`) through the real physics shows why this
self-check matters: under pure unperturbed two-body dynamics, the given
satellite/debris state vectors do **not** converge to the brief's
illustrative `baseline_miss_distance_km: 0.12` — the true two-body miss
distance for that scenario comes out to ~222 km. The agent still runs
correctly (30 candidates generated for 6 directions × 5 Δv options, all
ranked, `MNV-001` recommended), and importantly, it now surfaces that
discrepancy as a warning instead of hiding it:

```json
"warnings": [
  "Simulation result; Agent 6 must check mission constraints.",
  "Simplified two-body model: no J2/drag/luni-solar perturbations; impulsive burns; not valid for operational maneuver planning.",
  "Provided baseline_miss_distance_km (0.120 km) differs from this agent's own propagated no-maneuver miss distance (222.297 km); verify upstream inputs."
]
```

This is a genuinely useful integration-testing feature: if Agent 3/4
upstream ever hand Agent 5 a `baseline_miss_distance_km` that's
inconsistent with the raw state vectors also being passed along, you'll
see it immediately in the response instead of debugging silently-wrong
numbers later. For a tighter "before/after" demo moment, feed in state
vectors that are genuinely on a converging two-body trajectory (or reduce
`time_to_closest_approach_s` to match a real short-range encounter from
these particular vectors).
