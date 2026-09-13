"""One orchestration sequence; every agent receives explicit JSON contracts."""
import asyncio
from datetime import datetime,timezone
from app.agents.registry import get
from app.state import store,events
from app import schemas


async def call(name,*args,**kwargs):
    await events.emit(name,"started",f"Running {name}")
    try:
        result = await asyncio.to_thread(get(name),*args,**kwargs)
    except Exception as exc:
        await events.emit(name,"failed",str(exc))
        raise
    await events.emit(name,"completed",f"{name} completed")
    return result


async def run_maneuver_chain(primary_id,secondary_id=None,risk_tier=None,
                              pc_threshold_target=1e-6,constraints=None):
    risks=[r for r in store.all_risks() if r["primary_id"]==primary_id
           and (not secondary_id or r["secondary_id"]==secondary_id)]
    if not risks:
        raise ValueError("Run the simulation first: no risk record exists for this satellite")
    risk=max(risks,key=lambda r:r["pc"])
    tier=risk_tier or risk["risk_tier"]
    conj=store.get_conjunction(primary_id,risk["secondary_id"])
    candidates=[]
    evaluated=[]
    if tier in ("RED","ORANGE") or (tier=="YELLOW" and risk["time_to_tca_minutes"]<30):
        output=await call("maneuver",{**risk,"risk_tier":tier,"tca_utc":conj["tca_utc"],
                                     "pc_threshold_target":pc_threshold_target})
        schemas.ManeuverCandidatesResponse.model_validate(output)
        store.save_maneuver_candidates(primary_id,output)
        candidates=output["candidates"]
        limits={**(store.get_constraints("global") or {}),**(constraints or {})}
        output=await call("mission_constraint",primary_id,candidates,limits)
        schemas.EvaluatedCandidatesResponse.model_validate(output)
        store.save_evaluated_candidates(primary_id,output)
        evaluated=output["evaluated"]
    chosen=await call("decision",primary_id,tier,candidates,evaluated,pc_threshold_target)
    schemas.Decision.model_validate(chosen)
    store.save_decision(chosen)
    report=await call("explanation","report",decision=chosen,risk=risk)
    store.save_constraints(f"report:{primary_id}",report)
    return {"candidates":candidates,"evaluated":evaluated,"decision":chosen,"report":report}


async def run_full_pipeline(object_count=20,window_hours=2,guaranteed_conjunctions=1,
                            pc_threshold_target=1e-6):
    epoch=datetime.now(timezone.utc).replace(microsecond=0)
    store.reset_run()
    objects=await call("tracking",mode="synthetic",object_count=object_count,
                       guaranteed_conjunctions=guaranteed_conjunctions,epoch_utc=epoch)
    for obj in objects:
        schemas.TrackedObject.model_validate(obj)
        store.save_object(obj)
    await events.emit("prediction","started",f"Propagating {len(objects)} objects with SGP4")
    ephemerides=[]
    for obj in objects:
        eph=await asyncio.to_thread(get("prediction"),obj,window_hours,30)
        schemas.EphemerisRecord.model_validate(eph)
        store.save_ephemeris(eph)
        ephemerides.append(eph)
    await events.emit("prediction","completed",f"{len(objects)} ephemerides ready")
    output=await call("collision_detection",ephemerides)
    store.save_conjunctions(output["conjunctions"])
    await events.emit("collision_detection","completed",f"{len(output['conjunctions'])} close approaches")
    worst={}
    failures=[]
    for conj in output["conjunctions"]:
        try:
            risk=await call("risk_assessment",conj,epoch)
            schemas.RiskRecord.model_validate(risk)
            store.save_risk(risk)
            primary=risk["primary_id"]
            if primary not in worst or risk["pc"]>worst[primary]["pc"]:
                worst[primary]=risk
        except Exception as exc:
            failures.append({"primary_id":conj["primary_id"],"error":str(exc)})
    decisions=[]
    for primary,risk in worst.items():
        try:
            result=await run_maneuver_chain(primary,risk["secondary_id"],
                                            pc_threshold_target=pc_threshold_target)
            decisions.append(result["decision"])
        except Exception as exc:
            failures.append({"primary_id":primary,"error":str(exc)})
            await events.emit("pipeline","failed",f"Branch {primary}: {exc}")
    await events.emit("negotiation","skipped","Optional: use /negotiate for two supplied satellite proposals")
    return {"objects_tracked":len(objects),"pairs_screened":output["pairs_screened"],
            "conjunctions_flagged":len(output["conjunctions"]),"decisions":decisions,"failures":failures}
