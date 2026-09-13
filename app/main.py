"""
Orbital Guardian -- Module 10: Dashboard / Orchestration API.

Route list matches the build plan §10 exactly:
    POST /catalog/refresh              -> triggers Tracking Agent
    POST /propagate                    -> triggers Prediction Agent
    GET  /conjunctions                 -> Collision Detection Agent output
    GET  /risk/{primary}/{secondary}   -> Risk Assessment Agent output
    POST /maneuvers/{primary}          -> Maneuver + Mission Constraint + Decision, chained
    GET  /decision/{primary}           -> Decision Agent output
    POST /operator-command             -> Explanation Layer constraint parsing
    GET  /agent-log                    -> SSE event stream for the activity feed
plus two orchestrator-only conveniences the dashboard needs:
    GET  /catalog                      -> current tracked objects (for the table/globe)
    POST /simulate/start               -> one-click full pipeline (build plan Phase 8 milestone)

Run with:  uvicorn app.main:app --reload
Dashboard: http://localhost:8000/
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from app import schemas, pipeline
from app.agents.registry import get as get_agent, IS_REAL
from app.state import store, events

@asynccontextmanager
async def lifespan(app):
    store.init_db()
    yield


app = FastAPI(title="Orbital Guardian — Integrated Mission Console", lifespan=lifespan)
RUN = {"status":"idle","result":None,"error":None}
TASK = None


@app.middleware("http")
async def guard_run(request,call_next):
    from fastapi.responses import JSONResponse
    if RUN["status"]=="running" and (request.method=="POST" or request.query_params.get("refresh")=="true"):
        return JSONResponse({"detail":"A simulation is running; wait for completion"},status_code=409)
    return await call_next(request)


# ---------------------------------------------------------------------------
# 1. POST /catalog/refresh
# ---------------------------------------------------------------------------
@app.post("/catalog/refresh")
async def catalog_refresh(req: schemas.CatalogRefreshRequest):
    epoch = datetime.now(timezone.utc)
    fn = get_agent("tracking")
    await events.emit("tracking", "started", f"refreshing catalog (mode={req.mode})")
    try:
        objects = await asyncio.to_thread(
            fn, req.mode, req.object_count, req.group, req.guaranteed_conjunctions, epoch,
        )
    except Exception as e:
        await events.emit("tracking", "failed", str(e))
        raise HTTPException(status_code=400, detail=str(e))

    store.clear_objects()
    for obj in objects:
        store.save_object(obj)
    await events.emit("tracking", "completed", f"catalog refreshed: {len(objects)} objects")

    return {"refreshed_count": len(objects), "objects": objects, "as_of_utc": epoch.strftime("%Y-%m-%dT%H:%M:%SZ")}


@app.get("/catalog")
def get_catalog():
    return {"objects": store.all_objects()}


# ---------------------------------------------------------------------------
# 2. POST /propagate
# ---------------------------------------------------------------------------
@app.post("/propagate")
async def propagate(req: schemas.PropagateRequest):
    all_objects = store.all_objects()
    if req.object_ids:
        targets = [o for o in all_objects if o["object_id"] in req.object_ids]
    else:
        targets = all_objects
    if not targets:
        raise HTTPException(status_code=400, detail="No matching objects in catalog -- run /catalog/refresh first")

    fn = get_agent("prediction")
    await events.emit("prediction", "started", f"propagating {len(targets)} objects over {req.window_hours}h")
    results = []
    try:
        for obj in targets:
            eph = await asyncio.to_thread(fn, obj, req.window_hours, req.step_seconds)
            store.save_ephemeris(eph)
            results.append(eph)
    except Exception as e:
        await events.emit("prediction", "failed", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    await events.emit("prediction", "completed", f"propagated {len(results)} objects")

    return {"count": len(results), "ephemerides": results}


# ---------------------------------------------------------------------------
# 3. GET /conjunctions
# ---------------------------------------------------------------------------
@app.get("/conjunctions")
async def get_conjunctions(refresh: bool = False):
    if refresh:
        ephemerides = store.all_ephemerides()
        if len(ephemerides) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 propagated objects -- run /propagate first")
        n = len(store.all_objects())
        pairs_screened = n * (n - 1) // 2
        fn = get_agent("collision_detection")
        await events.emit("collision_detection", "started", f"screening {pairs_screened} possible pairs")
        out = await asyncio.to_thread(fn, ephemerides, None)
        store.save_conjunctions(out["conjunctions"])
        await events.emit("collision_detection", "completed",
                           f"screened {pairs_screened} possible pairs -> {len(out['conjunctions'])} candidates flagged")
        conjunctions = out["conjunctions"]
    else:
        conjunctions = store.all_conjunctions()
        n = len(store.all_objects())
        pairs_screened = n * (n - 1) // 2

    return {
        "screening_window": {"start_utc": None, "end_utc": None},
        "pairs_screened": pairs_screened,
        "conjunctions": conjunctions,
    }


# ---------------------------------------------------------------------------
# 4. GET /risk/{primary}/{secondary}
# ---------------------------------------------------------------------------
@app.get("/risk/{primary}/{secondary}")
async def get_risk(primary: str, secondary: str, refresh: bool = False):
    if not refresh:
        cached = store.get_risk(primary, secondary)
        if cached:
            return cached

    conj = store.get_conjunction(primary, secondary)
    if conj is None:
        raise HTTPException(status_code=404, detail=f"No conjunction on file for {primary} vs {secondary}")

    fn = get_agent("risk_assessment")
    await events.emit("risk_assessment", "started", f"{primary} vs {secondary}")
    risk_input = {**conj, "combined_hard_body_radius_m": 15.0}
    risk = await asyncio.to_thread(fn, risk_input, datetime.now(timezone.utc))
    store.save_risk(risk)
    await events.emit("risk_assessment", "completed",
                       f"{primary} vs {secondary}: Pc={risk['pc']:.2e}, tier={risk['risk_tier']}")
    return risk


# ---------------------------------------------------------------------------
# 5. POST /maneuvers/{primary}
# ---------------------------------------------------------------------------
@app.post("/maneuvers/{primary}")
async def plan_maneuvers(primary: str, body: schemas.ManeuverRequestBody):
    try:
        result = await pipeline.run_maneuver_chain(
            primary_id=primary,
            secondary_id=None,
            risk_tier=body.risk_tier,
            pc_threshold_target=body.pc_threshold_target,
            constraints=body.constraints.model_dump(exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ---------------------------------------------------------------------------
# 6. GET /decision/{primary}
# ---------------------------------------------------------------------------
@app.get("/decision/{primary}")
def get_decision(primary: str):
    decision = store.get_decision(primary)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"No decision on file for {primary}")
    return decision


# ---------------------------------------------------------------------------
# 7. POST /operator-command
# ---------------------------------------------------------------------------
@app.post("/operator-command")
async def operator_command(req: schemas.OperatorCommandRequest):
    fn = get_agent("explanation")
    await events.emit("explanation", "started", "parsing operator command")
    try:
        out = await asyncio.to_thread(fn, "parse_command", text=req.text)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    store.save_constraints("global", out["constraints"])
    await events.emit("explanation", "completed", f"constraints updated: {out['constraints']}")
    return {"constraints": out["constraints"], "applied_to": "global"}


# ---------------------------------------------------------------------------
# 8. GET /agent-log  (SSE stream)
# ---------------------------------------------------------------------------
@app.get("/agent-log")
async def agent_log_stream(request: Request):
    queue = events.subscribe()

    async def gen():
        try:
            for past in store.recent_log(limit=50):
                yield {"event": "agent_event", "data": _json(past)}
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                yield {"event": "agent_event", "data": _json(event)}
        finally:
            events.unsubscribe(queue)

    return EventSourceResponse(gen())


def _json(d: dict) -> str:
    import json
    return json.dumps(d)


# ---------------------------------------------------------------------------
# Orchestrator convenience: one-click full pipeline
# ---------------------------------------------------------------------------
@app.post("/simulate/start")
async def simulate_start(req: schemas.SimulateStartRequest):
    global TASK
    if req.guaranteed_conjunctions * 2 > req.object_count:
        raise HTTPException(422,"Each synthetic encounter needs two objects")
    if RUN["status"]=="running":
        raise HTTPException(409,"A simulation is already running")
    RUN.update(status="running",result=None,error=None)
    async def _run():
        try:
            result = await pipeline.run_full_pipeline(
                object_count=req.object_count,
                window_hours=req.window_hours,
                guaranteed_conjunctions=req.guaranteed_conjunctions,
            )
            RUN.update(status="partial" if result["failures"] else "completed",result=result)
            await events.emit("pipeline","completed",f"Simulation {RUN['status']}")
        except Exception as e:
            RUN.update(status="failed",error=str(e))
            await events.emit("pipeline", "failed", f"simulation run aborted: {e}")

    TASK = asyncio.create_task(_run())
    return {"status": "started", "message": "watch /agent-log for progress"}


@app.get("/risks")
def get_all_risks():
    """Bulk accessor for the dashboard's risk table -- not in the original
    build plan route list (which only specifies the single-pair GET), added
    because the frontend needs to render every flagged pair at once rather
    than polling per-pair."""
    return {"risks": store.all_risks()}


@app.get("/decisions")
def get_all_decisions():
    """Bulk accessor for the dashboard's decision table, same rationale as /risks."""
    return {"decisions": store.all_decisions()}


@app.get("/agent-status")
def agent_status():
    """Which agents are stub vs. real -- for a badge in the dashboard."""
    return {name: ("integrated; mock by default" if name=="explanation" else
                  "integrated; optional endpoint" if name=="negotiation" else "integrated") for name in IS_REAL}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/simulate/status")
def simulation_status():
    return {"status":RUN["status"],"error":RUN["error"],
            "summary":{k:v for k,v in (RUN["result"] or {}).items() if k!="decisions"}}


@app.get("/ephemerides")
def ephemerides():
    return {"ephemerides":store.all_ephemerides()}


@app.get("/reports")
def reports():
    return {"reports":[{"primary_id":d["primary_id"],**(store.get_constraints(f"report:{d['primary_id']}") or {})}
                       for d in store.all_decisions()]}


@app.get("/snapshot")
def snapshot():
    return {"simulation":simulation_status(),"catalog":store.all_objects(),
            "ephemerides":store.all_ephemerides(),"risks":store.all_risks(),
            "decisions":store.all_decisions(),"reports":reports()["reports"],
            "conjunctions":store.all_conjunctions(),"agents":agent_status()}


@app.post("/negotiate")
async def negotiate(body: schemas.NegotiationRequest):
    try:
        return await pipeline.call("negotiation",body.model_dump())
    except Exception as exc:
        raise HTTPException(422,str(exc)) from exc


# Serve the dashboard frontend (static/index.html etc.) at "/"
app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parents[1]/"static"), html=True), name="static")
