# Prediction Agent (Orbit Propagation)

Implements module 2 of the Orbital Guardian pipeline: SGP4 propagation +
mandatory TEME → ECI_J2000 frame conversion. Turns a Tracking Agent record
into the ephemeris the Collision Detection Agent needs.

Validated (see `tests/`): matches Skyfield's independent direct-TLE-parse
path to well under SGP4's own ~1-3 km/24h accuracy floor, and the
vectorized batch path matches the single-object path bit-for-bit.

## Install

```bash
pip install sgp4 skyfield numpy pydantic fastapi uvicorn
```

## Layout

```
prediction_agent/
  prediction_agent/
    models.py   # pydantic models = the exact JSON contract, input and output
    core.py     # single-object path: build_satrec() + Skyfield frame conversion
    batch.py    # vectorized SatrecArray path for catalog-scale propagation
    api.py      # FastAPI wrapper: POST /propagate, POST /propagate/batch
  tests/
    test_propagation.py   # contract-shape, cross-check, staleness, batch-parity tests
  examples/
    demo_two_object_conjunction.py   # the build plan's Phase 2 demo target
```

## Three ways another agent (or teammate) can use this

**1. Import it directly (same process, e.g. inside the Dashboard's orchestrator):**

```python
from prediction_agent.core import propagate
from prediction_agent.models import PredictionRequest

request = PredictionRequest(**tracking_agent_record_plus_window)
ephemeris = propagate(request)   # -> PredictionOutput, matches the contract exactly
```

**2. Call it over HTTP (separate process/service, matches the Dashboard's `POST /propagate` route):**

```bash
uvicorn prediction_agent.api:app --port 8001
curl -X POST http://localhost:8001/propagate -d @tracking_record.json
```

**3. Feed it a whole catalog at once (what the Collision Detection Agent's screening window wants):**

```python
from prediction_agent.batch import batch_propagate
from prediction_agent.models import BatchPredictionRequest

output = batch_propagate(BatchPredictionRequest(
    propagate_from_utc=..., propagate_to_utc=..., step_seconds=60,
    objects=[tracking_record_1, tracking_record_2, ...],
))
# output.predictions is a list of the exact same PredictionOutput shape,
# one per object -- feed straight into the Collision Detection Agent.
```

## Run the demo (no other agent needs to exist yet)

```bash
cd examples && python3 demo_two_object_conjunction.py
```

Propagates two synthetic objects over 24h, writes each one's ephemeris to
`examples/output/*.json` — **these files are ready-made fixture data** for
whoever builds the Collision Detection Agent next, since they're already
in that agent's expected input shape — and prints the minimum separation,
per the build plan's Phase 2 demo target.

## Run the validation suite

```bash
pip install pytest
pytest tests/ -v
```

Six checks: output shape matches the contract, no false-positive warnings
on a healthy recent epoch, the >7-day staleness warning fires correctly,
an independent cross-check against Skyfield's own direct-TLE-text parsing
path, graceful (non-crashing) handling of a physically invalid element
set, and exact numerical agreement between the single-object and batch
propagation paths.

## Design notes for whoever builds the next agent (Collision Detection)

- **Frame:** everything in `ephemeris[].r_km` / `v_kms` is already
  `ECI_J2000` (GCRS in Skyfield's terms) — no further rotation needed
  before doing 3D distance math or plotting on a globe.
- **Units:** km and km/s throughout, matching the contract.
- **Failure signaling:** a request that can't be propagated (invalid
  elements, decayed orbit) returns `ephemeris: []` plus a `warnings` list
  explaining why, rather than raising — check `warnings` before assuming
  an empty ephemeris means "no data yet."
- **Staleness:** a `warnings` entry appears if the propagation window is
  more than 7 days from the element epoch (B* drag fit going stale). This
  doesn't block anything, but the Risk Assessment Agent downstream may
  want to fold it into confidence scoring.
- **Scale:** for a handful of objects, call `propagate()` in a loop; for
  a full catalog screening pass, use `batch_propagate()` — both are
  numerically identical, the batch path just amortizes the SatrecArray
  and rotation-matrix setup across every object instead of paying it
  per-object.
