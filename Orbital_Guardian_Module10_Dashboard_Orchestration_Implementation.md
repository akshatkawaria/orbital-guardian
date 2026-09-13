# Orbital Guardian — Module 10: Dashboard / Orchestration API
## Implementation Deep-Dive

Companion to `Orbital_Guardian_Build_Plan.md` (§10) and `Orbital_Guardian_Research_Reference.md`. That document defines *what* Module 10 must expose (the endpoint list and demo target). This document defines *how* to actually build it: the orchestration engine, state model, streaming design, endpoint-by-endpoint behavior with full I/O payloads, frontend wiring, and failure handling — so you can start writing code from this directly.

---

## 1. What "Orchestration API" actually means here

Modules 1–9 are **pure functions over JSON**: give one a valid input document, get a valid output document, no shared memory. Module 10 is the only module that is allowed to have state and side effects. Its job is threefold, and it's worth naming these as three separate concerns because they get tangled if you don't:

1. **Orchestrator** — calls agents 1→9 in the right order, in the right shape, and persists each result so the next agent (or the frontend) can read it back.
2. **API surface** — a stable REST contract the frontend (and demo operators) talk to, independent of whichever agents are stubbed vs. real on a given day.
3. **Event bus** — turns "agent N just ran" into a live stream the dashboard can render as the scrolling Agent Activity Log — the single UI element the build plan calls out as doing the most to sell "this is agentic" to judges.

Because agents 1–9 are stateless JSON-in/JSON-out functions, module 10 can call them **in-process** (as Python function/class calls) rather than as separate microservices. This is the right call for a hackathon: no network hops, no service discovery, no docker-compose — just an `agents/` package where each submodule exposes one function matching its documented contract. If a teammate hasn't finished their agent yet, you drop in a `fixtures/<agent>.json` and a thin wrapper that returns it — the orchestrator can't tell the difference, which is the entire point of the JSON-contract design rule in §0 of the build plan.

```
orchestrator/
  agents/
    tracking.py            # def run(tle_batch) -> CatalogEntry
    prediction.py          # def run(catalog_entry, window) -> Ephemeris
    collision_detection.py # def run(ephemerides, window) -> Conjunctions
    risk_assessment.py     # def run(conjunction, physical_params) -> RiskRecord
    maneuver.py            # def run(risk_record, state) -> ManeuverCandidates
    mission_constraint.py  # def run(candidates, constraints) -> EvaluatedCandidates
    decision.py            # def run(risk_record, approved) -> DecisionRecord
    negotiation.py         # def run(proposals) -> NegotiationResult
    explanation.py         # def run(decision_or_text) -> ReportText | Constraints
    registry.py            # maps agent name -> real impl or fixture stub
  state/
    store.py               # SQLite/SQLModel tables, one per contract type
    events.py              # in-memory pub/sub for the activity log
  api/
    main.py                # FastAPI app, routes from §464-474 of build plan
    schemas.py              # Pydantic models mirroring every JSON contract exactly
  pipeline.py               # the actual orchestration graph (see §3 below)
```

**Design rule for this layer:** the orchestrator never computes orbital mechanics, Pc, or Δv itself — same discipline the build plan enforces on the LLM layer (§9). If you ever find yourself writing math in `api/main.py`, that logic belongs in an agent module, not the orchestrator.

---

## 2. State model

Pick SQLite (via `SQLModel` or raw `sqlite3`) over pure in-memory dicts even for a hackathon — you want the catalog to survive a server reload during the demo, and you get free persistence of the agent log for post-mortem debugging. One table per contract type, keyed by the same IDs the JSON contracts already use:

| Table | Primary key | Populated by | Read by |
|---|---|---|---|
| `objects` | `object_id` | Tracking Agent | Prediction, Dashboard |
| `ephemerides` | `object_id` + window | Prediction Agent | Collision Detection, Globe |
| `conjunctions` | `primary_id + secondary_id + tca_utc` | Collision Detection | Risk Assessment, Dashboard |
| `risk_records` | `primary_id + secondary_id` | Risk Assessment | Maneuver, Decision, Dashboard |
| `maneuver_candidates` | `primary_id` | Maneuver Agent | Mission Constraint |
| `evaluated_candidates` | `primary_id` | Mission Constraint | Decision |
| `decisions` | `primary_id` | Decision Agent | Negotiation, Explanation, Dashboard |
| `constraints` | `primary_id` (or global) | Explanation Agent (NL parsing) or operator UI | Mission Constraint |
| `agent_log` | autoincrement `event_id` | every agent invocation | SSE stream, Dashboard log panel |

Each row stores the **raw JSON blob** in a `payload` column plus 2–3 indexed columns for lookups (object IDs, timestamps, risk tier). This means you never need a schema migration when an agent's JSON contract gains a field mid-hackathon — you just start writing the new key into the blob. `schemas.py` (Pydantic) is still the source of truth for validation on the way in and out of the API, so contract violations get caught even though storage is loose.

---

## 3. The orchestration graph

The 9 agents form a DAG, not a straight line — module 4 fans out to modules 5 and 6, which fan back in to module 7 (see the build plan's ASCII diagram). Model this explicitly rather than hardcoding call order inline in each route handler, or the branching logic (parallel maneuver + constraint evaluation) turns into spaghetti fast.

```python
# pipeline.py
from dataclasses import dataclass
from typing import Callable
import asyncio

@dataclass
class Step:
    name: str                 # matches agent_log "agent" field
    fn: Callable               # agents.registry.get(name)
    depends_on: list[str]      # upstream step names

PIPELINE = [
    Step("tracking", tracking.run, depends_on=[]),
    Step("prediction", prediction.run, depends_on=["tracking"]),
    Step("collision_detection", collision_detection.run, depends_on=["prediction"]),
    Step("risk_assessment", risk_assessment.run, depends_on=["collision_detection"]),
    Step("maneuver", maneuver.run, depends_on=["risk_assessment"]),
    Step("mission_constraint", mission_constraint.run, depends_on=["risk_assessment"]),  # parallel with maneuver's consumer
    Step("decision", decision.run, depends_on=["maneuver", "mission_constraint"]),
    Step("negotiation", negotiation.run, depends_on=["decision"]),           # optional
    Step("explanation", explanation.run, depends_on=["decision"]),
]

async def run_pipeline(context: dict, emit):
    """context carries IDs/results keyed by step name; emit() pushes agent_log events."""
    completed = {}
    for step in PIPELINE:
        if not all(dep in completed for dep in step.depends_on):
            continue  # simple topological pass; fine at this DAG size
        await emit(agent=step.name, status="started", detail=f"{step.name} invoked")
        try:
            result = await asyncio.to_thread(step.fn, *[completed[d] for d in step.depends_on] or [context])
            completed[step.name] = result
            await emit(agent=step.name, status="completed", detail=summarize(result))
        except Exception as e:
            await emit(agent=step.name, status="failed", detail=str(e))
            raise
    return completed
```

Notes on this shape:
- `asyncio.to_thread` matters because Chan's Pc method, SGP4 propagation, and the CW-equation maneuver search are all CPU-bound numeric code — running them directly in an `async def` route would block FastAPI's event loop and freeze the SSE stream you're trying to demo. Thread-offloading keeps the log stream live *while* the math runs.
- `emit` is the same function whether it's called from `run_pipeline` (the "Start Simulation" one-click demo path) or from an individual route handler that only wants to trigger one agent (e.g. `POST /catalog/refresh` running only the Tracking Agent). Every agent invocation, whether part of a full run or a single targeted call, produces a log event — this is what makes the log feel alive even when a judge clicks one button at a time instead of "Start Simulation."
- For the two-satellite negotiation branch (agent 8) and the LLM explanation layer (agent 9), treat them as **leaves off the Decision step**, not blockers — a demo run should complete and show a decision even if negotiation/explanation are still stubbed.

---

## 4. Endpoint-by-endpoint implementation

Building on the route list already given in the build plan (§10), here is what each one actually does internally, with concrete request/response bodies.

### `POST /catalog/refresh`
Triggers the Tracking Agent over either a CelesTrak pull or the synthetic fleet generator.

**Request**
```json
{ "mode": "synthetic", "object_count": 50 }
```
or
```json
{ "mode": "celestrak", "group": "active" }
```

**Response** — array of Tracking Agent output records (exact schema from build plan §1), plus a summary the dashboard's catalog table binds directly to:
```json
{
  "refreshed_count": 50,
  "objects": [ /* Tracking Agent output records */ ],
  "as_of_utc": "2026-09-12T00:00:00Z"
}
```
Internally: calls `tracking.run()`, upserts into `objects` table, emits one `agent_log` event (`agent: "tracking", status: "completed", detail: "catalog refreshed: 50 objects"`).

### `POST /propagate`
Triggers Prediction Agent for one or all catalog objects over a window.

**Request**
```json
{ "object_ids": ["SAT-042", "DEB-891"], "window_hours": 24, "step_seconds": 60 }
```
Omitting `object_ids` propagates the whole catalog — needed for the "screen the full pairwise set" collision-detection demo beat.

**Response**: array of Prediction Agent outputs (build plan §2 schema), stored in `ephemerides`.

### `GET /conjunctions`
Read-only; returns the Collision Detection Agent's last output for the current screening window, optionally re-triggering it if `?refresh=true`.

**Response**
```json
{
  "screening_window": { "start_utc": "2026-09-12T00:00:00Z", "end_utc": "2026-09-13T00:00:00Z" },
  "pairs_screened": 1225,
  "conjunctions": [ /* build plan §3 schema, one entry per flagged pair */ ]
}
```
`pairs_screened` is what drives the "Screened 1,225 possible pairs → 3 candidates flagged" funnel counter called out as a demo target — surface it explicitly rather than making the frontend infer it from `N choose 2` of the catalog size, since the real filter cascade discards most pairs before the final count.

### `GET /risk/{primary}/{secondary}`
Runs (or returns cached) Risk Assessment Agent output for one specific pair.

**Response**: exact build plan §4 schema — `pc`, `pc_method`, `risk_tier`, `time_to_tca_minutes`, `miss_distance_km`. Cache by `(primary, secondary, tca_utc)` so repeated dashboard polling doesn't re-run Chan's method needlessly; invalidate the cache entry whenever `/propagate` produces new ephemerides for either object.

### `POST /maneuvers/{primary}`
The chained call: Maneuver Agent → Mission Constraint Agent → Decision Agent, run back to back for one object, matching the build plan's fan-out/fan-in shape.

**Request**
```json
{
  "risk_tier": "RED",
  "pc_threshold_target": 1e-6,
  "constraints": {
    "altitude_band_km": [540, 560],
    "protected_ground_regions": ["India"],
    "max_fuel_budget_pct_remaining": 0.5
  }
}
```

**Response** — return all three intermediate results, not just the final decision. The frontend needs the rejected candidates (with their `violations`) to render the "cheapest option got rejected because it would've hit SAT-107" beat; if you only return the Decision Agent's final answer, that story is invisible in the UI.
```json
{
  "candidates": [ /* Maneuver Agent §5 output */ ],
  "evaluated": [ /* Mission Constraint Agent §6 output */ ],
  "decision": { /* Decision Agent §7 output */ }
}
```
Internally this is exactly the `maneuver → mission_constraint → decision` slice of the pipeline graph in §3, invoked standalone so a single object can be re-planned (e.g. after an operator changes a constraint via `/operator-command`) without re-running the whole catalog through tracking/prediction/collision-detection again.

### `GET /decision/{primary}`
Read-only accessor for the last stored Decision Agent output — what the "before/after" screen polls.

### `POST /operator-command`
Feeds free text to the Explanation Layer's constraint-parsing mode (build plan §9), then writes the result into the `constraints` table so the *next* `/maneuvers/{primary}` call picks it up.

**Request**
```json
{ "text": "Avoid this collision while minimizing fuel consumption and don't reduce the satellite's communication coverage over India." }
```
**Response**
```json
{
  "constraints": { "protected_ground_regions": ["India"], "optimize_for": "min_fuel" },
  "applied_to": "global"
}
```
This is the endpoint behind the "live operator chat box" demo target — after it returns, the dashboard should immediately re-call `/maneuvers/{primary}` with the new constraints merged in, so the operator watches the candidate list visibly change without touching another control.

### `GET /agent-log`
Covered fully in §5 below — this is the streaming endpoint, not a plain GET.

---

## 5. The Agent Activity Log (the actual "money" UI element)

### SSE vs. WebSocket — pick SSE
The build plan leaves this open ("WebSocket or Server-Sent Events"). For this specific feature, **SSE is the better default**:
- The data only flows server → client (agent events), never client → server on the same channel — that's exactly the case SSE was designed for, whereas WebSocket is bidirectional machinery you don't need here.
- SSE runs over plain HTTP, so it survives typical hackathon-venue Wi-Fi/proxy setups that sometimes choke on WebSocket upgrades.
- The browser's native `EventSource` API reconnects automatically on drop, which you want during a live demo — you do not want to hand-roll reconnect logic under time pressure.

Reserve WebSocket for later only if you add bidirectional needs (e.g. an operator dragging a maneuver burn time live and seeing risk update instantly). For the hackathon scope, one SSE stream for the log is enough.

### Server implementation
Use `sse-starlette`'s `EventSourceResponse`, backed by an in-memory `asyncio.Queue` per connected client (a simple broadcaster), fed by the same `emit()` used inside `run_pipeline`:

```python
# state/events.py
import asyncio
subscribers: list[asyncio.Queue] = []

async def emit(agent: str, status: str, detail: str):
    event = {"agent": agent, "status": status, "detail": detail, "ts": utcnow_iso()}
    store_event(event)                       # persist to agent_log table too
    for q in subscribers:
        await q.put(event)

# api/main.py
from sse_starlette.sse import EventSourceResponse

@app.get("/agent-log")
async def agent_log_stream(request: Request):
    queue = asyncio.Queue()
    subscribers.append(queue)
    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                yield {"event": "agent_event", "data": json.dumps(event)}
        finally:
            subscribers.remove(queue)
    return EventSourceResponse(gen())
```

### Event schema
```json
{ "agent": "risk_assessment", "status": "completed", "detail": "SAT-042 vs DEB-891: Pc=3.4e-3, tier=RED", "ts": "2026-09-12T13:40:02Z" }
```
`status` is one of `started | completed | failed`. Keep `detail` a short one-line human summary (built with an f-string in each agent wrapper, *not* by calling the Explanation/LLM layer per event — that agent is for the final report, not per-tick log lines, and calling an LLM per pipeline step would add latency you don't want in a live log).

### Frontend consumption
```javascript
const es = new EventSource("/agent-log");
es.addEventListener("agent_event", (e) => {
  const evt = JSON.parse(e.data);
  appendToLogPanel(evt);       // scrolling list, color-coded by status
});
```

---

## 6. Frontend: globe, risk table, log panel

### 3D globe — CesiumJS
Recommended over Three.js specifically because CesiumJS ships built-in Earth imagery/terrain and a time-dynamic position primitive (`SampledPositionProperty`) purpose-built for exactly this ephemeris shape — you'd otherwise be hand-rolling both. Feed each object's `ephemeris` array from the Prediction Agent's output directly:

```javascript
const positionProperty = new Cesium.SampledPositionProperty();
ephemeris.forEach(pt => {
  positionProperty.addSample(
    Cesium.JulianDate.fromIso8601(pt.t_utc),
    Cesium.Cartesian3.fromArray(pt.r_km.map(km => km * 1000)) // km -> m
  );
});
viewer.entities.add({
  id: object_id,
  position: positionProperty,
  point: { pixelSize: 6, color: riskTierColor(risk_tier) },
  path: { show: true, width: 1 }
});
```
Two implementation details worth flagging because they're the most common bugs in exactly this kind of build:
- `r_km` from the Prediction Agent is in **ECI**, already converted from TEME per the build plan's mandatory-conversion note (§2) — Cesium's default `FixedFrame`/`InertialFrame` handling expects an inertial frame, so this conversion having already happened upstream is what makes the globe rendering correct instead of subtly wrong (satellites appearing to drift).
- Color each point/path by `risk_tier` (GREEN/YELLOW/ORANGE/RED) pulled from the Risk Assessment Agent's output for that object, not a static color — this is what turns the globe from "pretty" into "legible" for judges at a glance.
- For the two-orbit "compelling early demo checkpoint" (build plan §2), a static overlay of two paths plus a text readout of `min separation over window` (computed client-side or via a lightweight endpoint) is enough — don't wait for the full collision agent to be wired in before shipping this view.

### Risk table
A plain sortable table bound to `GET /conjunctions` + per-pair `GET /risk/{a}/{b}`, columns: primary, secondary, TCA, miss distance, Pc, tier (colored badge). Re-fetch on an interval or, better, push table updates over the same SSE channel by adding a second event type (`risk_update`) alongside `agent_event` so the table and the log animate in sync without separate polling.

### Agent log panel
Straightforward scrolling list bound to the `EventSource` handler above; color by `status` (grey=started, green=completed, red=failed). This is the element the build plan says matters most — keep it visually prominent (e.g. a persistent sidebar, not a collapsible drawer) since it's doing more demo-persuasion work than the globe.

### Suggested stack
FastAPI + `sse-starlette` (backend); React + CesiumJS via `resium` or the vanilla Cesium SDK (frontend); no separate task queue needed at hackathon scale — `asyncio.to_thread` per agent call is sufficient given the object counts involved (50–100 objects, low-thousands of pairs screened).

---

## 7. "Start Simulation" — the single-click full pipeline

One endpoint that ties everything together for the closing demo beat:

**`POST /simulate/start`**
```json
{ "object_count": 50, "window_hours": 24 }
```
Kicks off `run_pipeline()` as a background task (`asyncio.create_task`, or FastAPI's `BackgroundTasks` if you want it request-scoped) and returns immediately:
```json
{ "run_id": "run_20260912_134500", "status": "started" }
```
The frontend already has its `/agent-log` SSE connection open, so it doesn't need to poll this endpoint at all — every step of the run (tracking → prediction → collision detection → risk → maneuver/constraint → decision) shows up in the log panel and updates the globe/table live as each stage's results land in the state store. This is the "full pipeline, single click" milestone from the build plan's Phase 8 row.

---

## 8. Failure isolation and partial results

Because agents 1–9 are independently swappable stubs, the orchestrator has to assume any one of them might throw, return malformed JSON, or (during early integration) just not exist yet. Two rules keep the dashboard demo-safe:

1. **Validate at the boundary, not in the pipeline body.** Wrap every agent call so its output is checked against the Pydantic schema for that contract *before* it's written to the state store or handed to the next step. A schema violation should log a `status: "failed"` event with the validation error as `detail` and halt just that branch — not crash the FastAPI process.
2. **A failed agent doesn't blank the dashboard.** If, say, the Negotiation Agent (optional, §8) isn't ready, `run_pipeline` should still complete through Decision and Explanation; the UI should show "negotiation: not available" rather than erroring the whole run. This directly supports the build plan's stated build order — teams integrate incrementally, and the orchestrator needs to tolerate that gracefully rather than assuming a complete pipeline on day one.

---

## 9. Testing strategy specific to this module

Because every upstream contract is already schema-defined, Module 10's tests don't need real orbital mechanics at all:
- **Contract/fixture tests**: for each agent, drop in the exact example JSON from the build plan as a fixture and assert the orchestrator round-trips it correctly through the state store and back out the matching `GET` endpoint.
- **Pipeline shape test**: assert `run_pipeline` visits every `Step` whose dependencies are satisfied and skips those that aren't (covers the "agent not implemented yet" case above).
- **SSE smoke test**: connect a test client to `/agent-log`, trigger `POST /catalog/refresh`, assert at least one `agent_event` with `agent: "tracking"` arrives within a timeout.
- **End-to-end demo rehearsal**: run `POST /simulate/start` against the synthetic 50-object fleet with 2–3 objects deliberately on a collision course (per build plan §3's demo target) and confirm the log stream, risk table, and final decision all agree — this is your actual dress rehearsal for the live demo, not just a unit test.

---

## 10. Summary checklist

- [ ] `agents/registry.py` — real-or-fixture dispatch per agent name
- [ ] SQLite tables mirroring each JSON contract, keyed by the IDs already in the schemas
- [ ] `pipeline.py` DAG runner with thread-offloaded agent calls
- [ ] All 8 REST endpoints from the build plan, each documented above with full request/response bodies
- [ ] `/agent-log` SSE stream backed by a pub/sub queue, persisted to `agent_log` table
- [ ] CesiumJS globe consuming Prediction Agent ephemerides directly, colored by Risk Assessment tier
- [ ] Risk table + log panel both live off the same event stream
- [ ] `/simulate/start` one-click full-pipeline trigger
- [ ] Schema validation at every agent-call boundary, with failure isolation per branch
- [ ] Fixture-based contract tests for all 9 upstream agents

---

## References used for this implementation design
- FastAPI Server-Sent Events documentation and `sse-starlette` (production SSE implementation for Starlette/FastAPI): standard pattern for `EventSourceResponse` + async generator streaming used in §5.
- CesiumJS `SampledPositionProperty` / CZML time-dynamic positioning, as used in poliastro's CZML extractor and multiple open-source satellite trackers (`satvis`, `exosphere`, OrbPro) — confirms ECI-frame sampled positions are the standard way to drive a time-dynamic Cesium entity from a discrete ephemeris array, matching the Prediction Agent's exact output shape.
- Build plan §0 architecture diagram and §10 route list (source contract this document elaborates).
- `mooneedg/Starlink_Project` and `SatGuard` (cited in the research reference doc) as end-to-end prior art combining this same ingest → propagate → screen → risk → CesiumJS dashboard shape, useful as an integration-order sanity check.
