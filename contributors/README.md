# Tracking Agent — Orbital Guardian

Phase 1 of the build plan: maintain the authoritative current-state catalog
of every tracked object. This agent **parses and stores** — it does not
propagate, screen, or score anything (see `Tracking_Agent_Implementation_Guide.md`
for the full design rationale).

## Install

```bash
pip install -r requirements.txt
```

## Structure

```
tracking_agent/
  tle_utils.py       # checksum validation, Alpha-5 decode, name-based object_type fallback
  parser.py          # raw TLE -> contract JSON, via sgp4 (parsing only, no propagation)
  catalog.py          # SQLite-backed store: idempotent upsert, staleness, stats
  sources/
    celestrak.py       # real mode: CelesTrak GP API (no auth) — GROUP / CATNR / INTDES / NAME
    spacetrack.py       # real mode: Space-Track.org (session auth, rate-limited)
    synthetic.py         # demo mode: synthetic fleet generator with guaranteed conjunctions
  agent.py             # TrackingAgent — the one class other code should import
  api.py                # FastAPI wrapper: POST /catalog/refresh, GET /catalog, etc.
tests/                  # 22 tests, all passing — run with `pytest tests/`
```

## Quickstart (in-process, no server)

```python
from tracking_agent.agent import TrackingAgent

agent = TrackingAgent(db_path="tracking_catalog.db")

# Demo mode — no network needed, includes guaranteed-conjunction pairs
agent.refresh_from_synthetic(n_objects=50, n_conjunction_pairs=3)

# Real mode — CelesTrak, no auth required
agent.refresh_from_celestrak_group("stations")        # curated group
agent.refresh_from_celestrak_catnr(25544)              # single object (ISS)
agent.refresh_from_celestrak_intdes("2020-025")        # everything from one launch

# Real mode — Space-Track (optional, needs a free account)
agent.configure_spacetrack(identity="you@example.com", password="...")
agent.refresh_from_spacetrack_recent(epoch_days=30)

# Manual ingest — e.g. an operator-pasted TLE from the dashboard
agent.ingest_raw_tle(object_id="25544", line1=L1, line2=L2, object_name="ISS (ZARYA)")

# The feed every downstream agent should consume:
catalog = agent.get_catalog()          # list[dict], contract-shaped
one = agent.get_object("25544")
print(agent.stats())
```

## Quickstart (as an HTTP service, for other agents/the dashboard)

```bash
uvicorn tracking_agent.api:app --reload --port 8001
```

```
POST /catalog/refresh    {"source": "synthetic", "n_objects": 50, "n_conjunction_pairs": 3}
POST /catalog/refresh    {"source": "celestrak", "group": "active"}
POST /catalog/refresh    {"source": "celestrak", "catnr": 25544}
POST /catalog/refresh    {"source": "celestrak", "intdes": "2020-025"}
POST /catalog/refresh    {"source": "spacetrack"}   # requires configure_spacetrack() called server-side
POST /catalog/ingest     {"object_id": "...", "line1": "...", "line2": "...", "object_name": "..."}
GET  /catalog                       -> full contract-shaped catalog
GET  /catalog?object_type=DEBRIS    -> filtered
GET  /catalog/{object_id}           -> one record, 404 if missing
GET  /catalog/stats/summary         -> counts by type + staleness
```

## Output contract (exact shape, matches build plan sec. 1)

```json
{
  "object_id": "25544",
  "object_type": "PAYLOAD",
  "epoch_utc": "2026-09-12T00:00:00Z",
  "mean_elements": {
    "inclination_deg": 51.6464,
    "raan_deg": 320.1755,
    "eccentricity": 0.0007999,
    "arg_perigee_deg": 10.9066,
    "mean_anomaly_deg": 53.2893,
    "mean_motion_rev_day": 15.50437522,
    "bstar": 4.0858e-05
  },
  "last_updated": "2026-09-12T07:36:02Z",
  "source": "celestrak:group=stations"
}
```

**This is exactly what the Prediction Agent's input schema expects per object.**
No shared in-memory objects, no coupling to how this agent stores or fetched
data — just call `GET /catalog` (or `agent.get_catalog()` in-process) and feed
each record's `mean_elements` + `epoch_utc` into the propagator.

## What's covered from each data-source subpoint in the research reference

- **CelesTrak** (`sources/celestrak.py`): `GROUP`, `CATNR`, `INTDES`, `NAME` queries,
  JSON format, `OBJECT_TYPE` pulled straight from CelesTrak's SATCAT-backed field
  with a name-heuristic fallback when it's `UNKNOWN`.
- **Space-Track.org** (`sources/spacetrack.py`): session-cookie auth login,
  rate-limit-aware (one client instance reused, not re-authenticated per call),
  same normalized output shape as the CelesTrak path.
- **Demo/synthetic mode** (`sources/synthetic.py`): generates real, checksum-valid
  TLE line pairs (not just pre-parsed dicts) so the demo exercises the exact same
  `parser.py` path real ingest does; deliberately phases a configurable number of
  object pairs to guarantee findable conjunctions downstream.
- **Manual/CDM-adjacent ingest** (`agent.ingest_raw_tle`, `POST /catalog/ingest`):
  the "operator pastes a TLE" path, validated with the same checksum rules.

## Known simplifications (be aware of before extending)

- `SpaceTrackClient` cannot be exercised in this sandbox (no network egress to
  `space-track.org`) — it's implemented against documented API behavior but
  should be smoke-tested against a real account before a live demo.
- SQLite connection uses `check_same_thread=False` for the FastAPI wrapper's
  threadpool — fine for single-writer hackathon load; swap for a proper
  connection pool if you add concurrent write paths later.
- The TLE *line-encoding* helper in `sources/synthetic.py` (element values ->
  TLE text) is a demo-data-generation convenience only; real ingest never
  encodes TLEs, only decodes them.
