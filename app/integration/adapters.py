"""Real contributor implementations wired to Module 10. No fallback stubs.

SGP4 supplies the nominal trajectory. Agent 5 supplies impulsive two-body
candidate generation. Its differential displacement is added to the SGP4
nominal path, then closest approach is solved again. This is a documented
short-horizon demo approximation, not a post-burn SGP4 orbit determination.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import copy

import numpy as np
from scipy.integrate import solve_ivp
from scipy.interpolate import CubicHermiteSpline
from scipy.optimize import least_squares, minimize_scalar

from contributors.tracking_agent.sources.synthetic import generate_synthetic_fleet
from contributors.tracking_agent.agent import TrackingAgent
from contributors.prediction_agent.prediction_agent.core import propagate
from contributors.prediction_agent.prediction_agent.models import PredictionRequest
from contributors.collision_detection_agent.agent import run_collision_detection
from contributors.risk_assessment_package.risk_assessment.risk_agent import assess_risk
from contributors.maneuver_agent.app.models import ManeuverRequest
from contributors.maneuver_agent.app.maneuvers import generate_candidates
from contributors.maneuver_agent.app.frame import build_local_frame, direction_unit_vector
from contributors.maneuver_agent.app.propagation import _two_body_eom, propagate_two_body, MU_EARTH_KM3_S2
from contributors.mission_constraint_agent.agent import evaluate_candidates
from contributors.mission_constraint_agent.state_provider import InMemoryStateProvider
from contributors.decision_agent.decision_agent.decision_agent import DecisionAgent
from contributors.negotiation_agent.negotiation_agent import negotiate_from_dict
from contributors.agent9_explanation_llm.app.explanation_agent import ExplanationAgent
from app.state import store

COVARIANCE = (np.eye(3) * 0.01).tolist()  # assumed 100 m 1-sigma per object


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def prediction(obj, window_hours=2.0, step_seconds=30):
    start = obj.get("propagate_from_utc", obj["epoch_utc"])
    out = propagate(PredictionRequest.model_validate({
        **obj, "propagate_from_utc": start,
        "propagate_to_utc": iso(dt(start) + timedelta(hours=window_hours)),
        "step_seconds": step_seconds,
    })).model_dump(mode="json")
    if len(out["ephemeris"]) < 2:
        raise ValueError(f"Prediction failed for {obj['object_id']}: {out.get('warnings')}")
    return out


def tracking(mode="synthetic", object_count=20, group=None, guaranteed_conjunctions=1, epoch_utc=None):
    epoch = (epoch_utc or datetime.now(timezone.utc)).replace(microsecond=0)
    if mode == "celestrak":
        agent = TrackingAgent(db_path=":memory:")
        try:
            agent.refresh_from_celestrak_group(group or "stations")
            records = agent.get_catalog()[:object_count]
        finally:
            agent.close()
    else:
        records = generate_synthetic_fleet(object_count, guaranteed_conjunctions, epoch, 42)
        # Engineer close encounters using the actual SGP4 prediction code.
        # Adjust each generated secondary's mean elements until it passes
        # 120 m radially from the primary at a chosen future epoch.
        # This is synthetic scenario construction, not an observed event.
        for i in range(min(guaranteed_conjunctions, len(records)//2)):
            primary, secondary = records[2*i:2*i+2]
            encounter = epoch + timedelta(seconds=2400 + 300*i)
            def state_at(obj):
                return np.array(prediction({**obj, "propagate_from_utc": iso(encounter)}, 1/3600, 1)["ephemeris"][0]["r_km"])
            rp = state_at(primary)
            target = rp + 0.12 * rp / np.linalg.norm(rp)
            secondary["mean_elements"] = copy.deepcopy(primary["mean_elements"])
            secondary["mean_elements"]["inclination_deg"] += 2.0
            e = secondary["mean_elements"]
            x0 = [e["raan_deg"], e["mean_anomaly_deg"], e["mean_motion_rev_day"]]
            def residual(x):
                e.update(raan_deg=float(x[0] % 360), mean_anomaly_deg=float(x[1] % 360), mean_motion_rev_day=float(x[2]))
                return state_at(secondary) - target
            solved = least_squares(residual, x0, max_nfev=100, xtol=1e-11, ftol=1e-11, gtol=1e-11)
            if np.linalg.norm(residual(solved.x)) > 0.02:
                raise ValueError("Could not construct the synthetic encounter")
            secondary["object_type"] = "DEBRIS"
            for record in (primary, secondary):
                record["scenario"] = "synthetic tuned encounter; covariance assumed"
    for obj in records:
        obj["propagate_from_utc"] = iso(epoch)
    return records


def collision(ephemerides, window=None):
    start = max(e["ephemeris"][0]["t_utc"] for e in ephemerides)
    end = min(e["ephemeris"][-1]["t_utc"] for e in ephemerides)
    out = run_collision_detection({
        "screening_window": {"start_utc": start, "end_utc": end},
        "ephemerides": ephemerides,
        "object_types": {o["object_id"]: o["object_type"] for o in store.all_objects()},
    }, config={"pad_for_aliasing": True, "fine_step_s": 0.1, "screening_radius_km": 20.0})
    # Padding broadens the coarse screen, not the final reporting threshold.
    # Module 10 stores one record per pair: explicitly keep closest event.
    by_pair = {}
    for c in out["conjunctions"]:
        if c["miss_distance_km"] > 20 or dt(c["tca_utc"]) <= dt(start)+timedelta(seconds=60):
            continue
        if store.get_object(c["primary_id"])["object_type"] != "PAYLOAD":
            continue
        key = (c["primary_id"], c["secondary_id"])
        if key not in by_pair or c["miss_distance_km"] < by_pair[key]["miss_distance_km"]:
            by_pair[key] = c
    out["conjunctions"] = sorted(by_pair.values(), key=lambda c:c["miss_distance_km"])
    out["screening_window"] = {"start_utc":start,"end_utc":end}
    out["pairs_screened"] = len(ephemerides)*(len(ephemerides)-1)//2
    return out


def risk(conjunction, now=None):
    payload = {"position_covariance_primary_km2": COVARIANCE,
               "position_covariance_secondary_km2": COVARIANCE,
               "combined_hard_body_radius_m": 15.0, **conjunction}
    result = assess_risk(payload)
    result["miss_distance_km"] = conjunction["miss_distance_km"]
    if now:
        result["time_to_tca_minutes"] = (dt(conjunction["tca_utc"])-now).total_seconds()/60
    result["covariance_source"] = "assumed isotropic 100 m sigma per object; not measured"
    return result


def trajectory(eph, epoch):
    points = eph["ephemeris"]
    times = np.array([(dt(p["t_utc"])-epoch).total_seconds() for p in points])
    return CubicHermiteSpline(times, [p["r_km"] for p in points], [p["v_kms"] for p in points])


def closest(path_a, path_b, start, end):
    grid = np.linspace(start, end, max(3, int((end-start)/10)+1))
    values = np.sum((path_a(grid)-path_b(grid))**2, axis=1)
    indices = np.where((values[1:-1] <= values[:-2]) & (values[1:-1] <= values[2:]))[0]+1
    options = [(float(values[0]), start), (float(values[-1]), end)]
    for i in indices:
        opt = minimize_scalar(lambda t: float(np.sum((path_a(t)-path_b(t))**2)),
                              bounds=(grid[i-1],grid[i+1]), method="bounded",
                              options={"xatol":1e-6})
        options.append((opt.fun,opt.x))
    squared, when = min(options)
    return float(np.sqrt(squared)), float(when)


def maneuver(payload):
    primary = payload["primary_id"]
    conj = store.get_conjunction(primary, payload["secondary_id"])
    eph_a = store.get_ephemeris(primary)
    eph_b = store.get_ephemeris(conj["secondary_id"])
    epoch = dt(eph_a["ephemeris"][0]["t_utc"])
    tca = (dt(conj["tca_utc"])-epoch).total_seconds()
    burn_s = max(1.0, tca-1800)
    if burn_s >= tca:
        return {"primary_id":primary,"candidates":[],"warnings":["No lead time for a burn"]}
    pa,pb = trajectory(eph_a,epoch),trajectory(eph_b,epoch)
    end = min((dt(eph_a["ephemeris"][-1]["t_utc"])-epoch).total_seconds(), tca+1800)
    r0,v0 = pa(0),pa(0,1)
    req = ManeuverRequest.model_validate({
        "event_id":f"{primary}_vs_{conj['secondary_id']}","satellite_id":primary,
        "debris_id":conj["secondary_id"],"reference_time":epoch,
        "time_to_closest_approach_s":tca,"burn_lead_time_s":burn_s,
        "satellite_state":{"position_km":r0.tolist(),"velocity_km_s":v0.tolist()},
        "debris_state":{"position_km":pb(0).tolist(),"velocity_km_s":pb(0,1).tolist()},
        "baseline_miss_distance_km":conj["miss_distance_km"],"target_miss_distance_km":2,
        "allowed_directions":["prograde","radial_out","normal"],
        "delta_v_options_m_s":[0.1,0.2,0.4,0.8,1.5],"max_delta_v_m_s":1.5,
    })
    generated = generate_candidates(req)
    rb,vb = propagate_two_body(r0,v0,burn_s)
    frame = build_local_frame(rb,vb)
    def solution(v):
        s = solve_ivp(_two_body_eom,(0,end-burn_s),np.r_[rb,v],args=(MU_EARTH_KM3_S2,),
                      method="DOP853",rtol=1e-10,atol=1e-10,dense_output=True)
        if not s.success:
            raise ValueError(s.message)
        return s.sol
    nominal = solution(vb)
    output = []
    sampling = np.unique(np.r_[np.linspace(0,end,241),burn_s-0.001,burn_s,tca])
    for i,raw in enumerate(generated.candidates):
        moved = solution(vb + direction_unit_vector(frame,raw.direction)*raw.delta_v_m_s/1000)
        def corrected(t):
            arr = np.asarray(t)
            elapsed = np.maximum(0,arr-burn_s)
            delta = (moved(elapsed)[:3]-nominal(elapsed)[:3]).T
            return pa(t) + delta
        miss,when = closest(corrected,pb,burn_s,end)
        rel = corrected(when)-pb(when)
        h=0.01
        vel = (corrected(when+h)-corrected(when-h))/(2*h)-pb(when,1)
        pc = risk({**conj,"tca_utc":iso(epoch+timedelta(seconds=when)),
                   "relative_position_km":rel.tolist(),"relative_velocity_vec_kms":vel.tolist(),
                   "miss_distance_km":miss},epoch)["pc"]
        points = corrected(sampling)
        velocity_delta = (moved(np.maximum(0,sampling-burn_s))[3:]-nominal(np.maximum(0,sampling-burn_s))[3:]).T
        velocity_delta[sampling < burn_s] = 0
        velocities = pa(sampling,1)+velocity_delta
        path = [{"t_utc":iso(epoch+timedelta(seconds=float(t))),"r_km":r.tolist(),"v_kms":v.tolist()}
                for t,r,v in zip(sampling,points,velocities)]
        output.append({"maneuver_id":f"{primary}-M{i+1:02}","burn_time_utc":iso(epoch+timedelta(seconds=burn_s)),
            "direction":{"prograde":"along_track","radial_out":"radial","normal":"cross_track"}[raw.direction],
            "dv_ms":raw.delta_v_m_s,"predicted_miss_km":miss,"predicted_pc":pc,
            "predicted_tca_utc":iso(epoch+timedelta(seconds=when)),
            "improvement_km":miss-conj["miss_distance_km"],
            "fuel_cost_pct":100*raw.delta_v_m_s/100.0,
            "trajectory":path,"model":"two-body differential correction on SGP4",
            "screened_until_utc":iso(epoch+timedelta(seconds=end))})
    output.sort(key=lambda c:(c["predicted_pc"]>=payload["pc_threshold_target"],c["dv_ms"],c["predicted_pc"]))
    return {"primary_id":primary,"candidates":output,
            "assumptions":{"fuel_proxy":"percentage of assumed remaining 100 m/s delta-v budget",
                           "covariance":"fixed assumed covariance; no covariance propagation"}}


def constraints(primary,candidates,limits):
    eph = store.get_ephemeris(primary)
    first = eph["ephemeris"][0]
    provider = InMemoryStateProvider()
    n = store.get_object(primary)["mean_elements"]["mean_motion_rev_day"]*2*np.pi/86400
    provider.register(primary,first["r_km"],first["v_kms"],n)
    defaults = {"altitude_band_km":[400,800],"protected_ground_regions":[],
                "max_fuel_budget_pct_remaining":5.0}
    out = evaluate_candidates({"primary_id":primary,"candidates":candidates,
                               "constraints":{**defaults,**{k:v for k,v in limits.items() if v is not None}}},provider)
    # Added moving-object check: the submitted Agent 6 only checks fixed
    # snapshots. Re-screen differential trajectories against catalog paths.
    epoch = dt(first["t_utc"])
    risk_records = [r for r in store.all_risks() if r["primary_id"]==primary]
    original_secondary = max(risk_records,key=lambda r:r["pc"])["secondary_id"] if risk_records else None
    others = [(o["object_id"],trajectory(o,epoch)) for o in store.all_ephemerides()
              if o["object_id"] not in (primary,original_secondary)]
    for candidate,result in zip(candidates,out["evaluated"]):
        if candidate["improvement_km"] <= 0:
            result["violations"].append("does_not_improve_original_encounter")
        moved = trajectory({"ephemeris":candidate["trajectory"]},epoch)
        start = (dt(candidate["burn_time_utc"])-epoch).total_seconds()
        end = (dt(candidate["screened_until_utc"])-epoch).total_seconds()
        for oid,other in others:
            new,_ = closest(moved,other,start,end)
            old,_ = closest(trajectory(eph,epoch),other,start,end)
            if new < 2.0 and new < old-1e-3:
                result["violations"].append(f"worsens_secondary_conjunction:{oid}")
        result["approved"] = not result["violations"]
    return out


def decision(primary,tier,candidates,evaluated,target):
    risks = [r for r in store.all_risks() if r["primary_id"]==primary]
    current = max(risks,key=lambda r:r["pc"])
    by_id = {c["maneuver_id"]:c for c in candidates}
    merged = [{**by_id[e["maneuver_id"]],**e} for e in evaluated]
    result = DecisionAgent().decide({**current,"risk_tier":tier,"evaluated_candidates":merged,
                                     "pc_threshold_target":target})
    selected = result.get("selected_maneuver")
    if selected:
        selected.update(by_id[selected["maneuver_id"]])
    result["confidence_note"] = "rule-based score, not a calibrated safety probability"
    return result


@lru_cache(maxsize=1)
def explainer():
    return ExplanationAgent()


def explanation(action,decision=None,risk=None,text=None):
    if action == "parse_command":
        parsed,source = explainer().parse_constraints(text)
        return {"constraints":parsed,"source":source}
    trimmed = copy.deepcopy(decision)
    if trimmed.get("selected_maneuver"):
        trimmed["selected_maneuver"].pop("trajectory",None)
    report,source,ok = explainer().generate_report(trimmed,risk)
    return {"report_text":report,"source":source,"numeric_fidelity_ok":ok}


def negotiation(payload):
    return negotiate_from_dict(payload)


REGISTRY = {"tracking":tracking,"prediction":prediction,"collision_detection":collision,
            "risk_assessment":risk,"maneuver":maneuver,"mission_constraint":constraints,
            "decision":decision,"explanation":explanation,"negotiation":negotiation}
