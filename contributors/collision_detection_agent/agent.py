"""
Collision Detection Agent — Orbital Guardian, Agent 3 of 10.

Consumes the Prediction Agent's ephemeris contract and produces the
Collision Detection Agent contract consumed by the Risk Assessment Agent,
per Orbital_Guardian_Build_Plan.md §3.

Design:
  1. Build a smooth position/velocity interpolant per object from its
     (t, r, v) sample sequence (CubicHermiteSpline — uses both position
     AND velocity samples, since the Prediction Agent gives us both).
  2. Resample every object onto a common, uniform coarse time grid whose
     step size is chosen automatically so a fast, close pass can't be
     "stepped over" between samples (see _recommend_coarse_step_s).
  3. At each coarse timestep, KD-tree query all objects' positions for
     pairs within a generous screening radius. This replaces the classical
     apogee/perigee + orbit-path + time filter triad with a single spatial
     screen over the already-propagated position cloud (the pattern used
     by the open-source Starlink_Project KD-tree screener).
  4. Cluster each flagged pair's flagged timesteps into contiguous windows
     (so one continuous close pass isn't treated as N separate events).
  5. For each window, refine to the true time-of-closest-approach with a
     vectorized fine-grained scan using the same interpolants.

This agent does NOT propagate orbits (that's the Prediction Agent's job)
and does NOT compute probability-of-collision or risk tiers (that's the
Risk Assessment Agent's job). It is purely geometric: TCA, miss distance,
relative velocity.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from scipy.interpolate import CubicHermiteSpline
from scipy.spatial import KDTree

# --------------------------------------------------------------------------
# Tunables (all overridable via run_collision_detection(..., config=...))
# --------------------------------------------------------------------------

DEFAULT_SCREENING_RADIUS_KM = 20.0    # KD-tree flag radius at native cadence — a coarse screening
                                       # volume, not the actual danger threshold (that's HBR, handled
                                       # downstream by the Risk Assessment Agent). Tight enough to keep
                                       # the candidate funnel small; see ALIASING NOTE below.
DEFAULT_MAX_REL_VEL_KMS = 16.0        # worst-case LEO-vs-LEO closing speed, used only if pad_for_aliasing=True
DEFAULT_PAD_FOR_ALIASING = False      # see _coarse_screen docstring "ALIASING NOTE"
DEFAULT_FINE_WINDOW_S = 180.0         # +/- padding around a flagged cluster before refining
DEFAULT_FINE_STEP_S = 0.5             # resolution of the final TCA refine scan
DEFAULT_CLUSTER_GAP_S = 180.0         # merge flagged timesteps into one event if gaps <= this


class CollisionDetectionError(ValueError):
    """Raised on malformed or contract-violating input."""


# --------------------------------------------------------------------------
# Time helpers — all internal math happens in seconds-since-window-start;
# only the input parse / output format touch ISO-8601 strings.
# --------------------------------------------------------------------------

def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _to_seconds(ts: str, epoch: datetime) -> float:
    return (_parse_iso(ts) - epoch).total_seconds()


def _to_iso(seconds: float, epoch: datetime) -> str:
    dt = epoch + timedelta(seconds=float(seconds))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# --------------------------------------------------------------------------
# Step 1: interpolants
# --------------------------------------------------------------------------

def _build_interpolants(ephemeris: list[dict], epoch: datetime):
    """Return (r_interp, v_interp): callables mapping seconds -> (N,3) or (3,) arrays."""
    if len(ephemeris) < 2:
        raise CollisionDetectionError("ephemeris needs >= 2 samples to interpolate")

    t = np.array([_to_seconds(e["t_utc"], epoch) for e in ephemeris], dtype=float)
    if np.any(np.diff(t) <= 0):
        raise CollisionDetectionError("ephemeris samples must be strictly increasing in time")

    r = np.array([e["r_km"] for e in ephemeris], dtype=float)
    v = np.array([e["v_kms"] for e in ephemeris], dtype=float)

    splines = [CubicHermiteSpline(t, r[:, k], v[:, k]) for k in range(3)]
    deriv_splines = [s.derivative() for s in splines]

    t_min, t_max = t[0], t[-1]

    def r_interp(tt):
        tt = np.clip(tt, t_min, t_max)
        return np.stack([s(tt) for s in splines], axis=-1)

    def v_interp(tt):
        tt = np.clip(tt, t_min, t_max)
        return np.stack([s(tt) for s in deriv_splines], axis=-1)

    return r_interp, v_interp, (t_min, t_max)


# --------------------------------------------------------------------------
# Step 2/3: coarse KD-tree screen at native ephemeris cadence
# --------------------------------------------------------------------------
#
# ALIASING NOTE (read before changing DEFAULT_SCREENING_RADIUS_KM):
# This screen checks the position cloud at the SAME cadence the Prediction
# Agent already gave us (e.g. every 60s) -- it does not resample finer.
# That is deliberate: resampling every object to sub-second resolution just
# to run a KD-tree at every step is O(N log N) per step but with enough
# steps (e.g. 24h at 0.5s = ~170k steps) it becomes too slow for a live
# demo, for a benefit that mostly matters for adversarially-fast, exactly-
# between-samples encounters -- not the case your synthetic fleet needs to
# demonstrate.
#
# The residual risk: two objects closing at v_rel could, in principle,
# pass fully through the screening radius between two native samples
# without either sample falling inside it. The distance either sample
# could be "safely" apart and still guarantee a catch is
# `screening_radius_km + v_rel_kms * step_s`. If you need that hard
# guarantee (e.g. for a real safety-critical deployment, not a hackathon
# demo), set pad_for_aliasing=True in run_collision_detection's config,
# which pads the screening radius by max_rel_vel_kms * step_s. This will
# increase the candidate count (more pairs go to refine), trading funnel
# tightness for a formal no-miss guarantee.
#
# Cheap mitigation you can also apply independently: ask the Prediction
# Agent for a finer step_seconds (e.g. 20-30s instead of 60s) for objects
# you know are on fast-crossing (near-polar vs near-polar) geometries.

def _coarse_screen(
    ephemerides: list[dict],
    epoch: datetime,
    screening_radius_km: float,
    pad_for_aliasing: bool,
    max_rel_vel_kms: float,
) -> dict[tuple[int, int], list[float]]:
    """
    Returns {(i, j): [flagged_t_seconds, ...]} for every pair whose
    positions -- taken directly from the Prediction Agent's own sample
    grid, no resampling -- come within the (possibly padded) screening
    radius at any shared timestep. i < j indices into `ephemerides`.

    Assumes all objects share the same sample timestamps (true whenever
    they came from a single Prediction Agent batch call over one
    screening window, per the build plan's contract). If step sizes
    differ across objects, each object's own samples are still queried
    at its own timestamps against the others via nearest-in-time lookup.
    """
    radius = screening_radius_km
    if pad_for_aliasing:
        # use the coarsest step present across all objects for the pad
        step_s = max(
            np.min(np.diff([_to_seconds(e["t_utc"], epoch) for e in obj["ephemeris"]]))
            for obj in ephemerides
        )
        radius = screening_radius_km + max_rel_vel_kms * step_s

    # Fast path: identical timestamps across all objects (the common case).
    t_lists = [[e["t_utc"] for e in obj["ephemeris"]] for obj in ephemerides]
    same_grid = all(t_lists[0] == t for t in t_lists[1:])

    candidates: dict[tuple[int, int], list[float]] = {}

    if same_grid:
        n_steps = len(t_lists[0])
        positions = np.array(
            [[e["r_km"] for e in obj["ephemeris"]] for obj in ephemerides]
        )  # shape: (n_objects, n_steps, 3)
        positions = np.transpose(positions, (1, 0, 2))  # (n_steps, n_objects, 3)
        times_s = np.array([_to_seconds(t, epoch) for t in t_lists[0]])

        for k in range(n_steps):
            tree = KDTree(positions[k])
            pairs = tree.query_pairs(r=radius)
            for (i, j) in pairs:
                candidates.setdefault((i, j), []).append(float(times_s[k]))
    else:
        # Fallback for mismatched grids: interpolate all objects onto the
        # finest object's native grid, then run the same KD-tree pass.
        r_interps = []
        for obj in ephemerides:
            r_i, _v_i, _b = _build_interpolants(obj["ephemeris"], epoch)
            r_interps.append(r_i)
        finest = min(t_lists, key=len)
        times_s = np.array([_to_seconds(t, epoch) for t in finest])
        positions = np.stack([r(times_s) for r in r_interps], axis=1)  # (n_steps, n_objects, 3)

        for k in range(len(times_s)):
            tree = KDTree(positions[k])
            pairs = tree.query_pairs(r=radius)
            for (i, j) in pairs:
                candidates.setdefault((i, j), []).append(float(times_s[k]))

    return candidates


# --------------------------------------------------------------------------
# Step 4: cluster flagged timesteps into contiguous close-approach windows
# --------------------------------------------------------------------------

def _cluster_into_windows(times: list[float], gap_s: float) -> list[tuple[float, float]]:
    times = sorted(times)
    windows = []
    start = prev = times[0]
    for t in times[1:]:
        if t - prev > gap_s:
            windows.append((start, prev))
            start = t
        prev = t
    windows.append((start, prev))
    return windows


# --------------------------------------------------------------------------
# Step 5: precise TCA refine — vectorized fine-grained scan
# --------------------------------------------------------------------------

def _refine_tca(
    r_a, v_a, r_b, v_b,
    window: tuple[float, float],
    bounds: tuple[float, float],
    fine_window_s: float,
    fine_step_s: float,
) -> dict:
    t_lo = max(bounds[0], window[0] - fine_window_s)
    t_hi = min(bounds[1], window[1] + fine_window_s)
    t_fine = np.arange(t_lo, t_hi + fine_step_s, fine_step_s)

    d = r_b(t_fine) - r_a(t_fine)
    dist = np.linalg.norm(d, axis=-1)
    idx = int(np.argmin(dist))
    tca_t = float(t_fine[idx])

    d_tca = (r_b(tca_t) - r_a(tca_t)).reshape(3)
    v_tca = (v_b(tca_t) - v_a(tca_t)).reshape(3)

    return {
        "tca_t_seconds": tca_t,
        "miss_distance_km": float(np.linalg.norm(d_tca)),
        "relative_velocity_kms": float(np.linalg.norm(v_tca)),
        "relative_position_km": [float(x) for x in d_tca],
        "relative_velocity_vec_kms": [float(x) for x in v_tca],
    }


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def run_collision_detection(request: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Implements the Collision Detection Agent contract:

    Input  (Orbital_Guardian_Build_Plan.md §3):
        {
          "screening_window": {"start_utc": ..., "end_utc": ...},
          "ephemerides": [ <Prediction Agent output>, ... ]
        }

    Optional extra input field (not required by the contract, safely
    ignorable by strict consumers): "object_types": {object_id: "PAYLOAD"|
    "ROCKET_BODY"|"DEBRIS", ...} — used only to pick a stable primary/
    secondary convention (PAYLOAD preferred as primary). If absent, falls
    back to alphabetical object_id ordering.

    Output:
        {
          "conjunctions": [ {primary_id, secondary_id, tca_utc,
                              miss_distance_km, relative_velocity_kms,
                              relative_position_km,
                              relative_velocity_vec_kms}, ... ],
          "screening_summary": {                      # additive, for the
              "n_objects": int,                         # dashboard funnel
              "n_pairs_screened": int,                   # counter / agent
              "n_conjunctions_flagged": int,              # activity log
              "screening_radius_km": float,
              "pad_for_aliasing": bool,
          }
        }
    """
    cfg = {
        "screening_radius_km": DEFAULT_SCREENING_RADIUS_KM,
        "max_rel_vel_kms": DEFAULT_MAX_REL_VEL_KMS,
        "pad_for_aliasing": DEFAULT_PAD_FOR_ALIASING,
        "fine_window_s": DEFAULT_FINE_WINDOW_S,
        "fine_step_s": DEFAULT_FINE_STEP_S,
        "cluster_gap_s": DEFAULT_CLUSTER_GAP_S,
    }
    if config:
        cfg.update(config)

    # ---- validate & unpack ----
    if "screening_window" not in request or "ephemerides" not in request:
        raise CollisionDetectionError("request must contain 'screening_window' and 'ephemerides'")

    window_spec = request["screening_window"]
    ephemerides = request["ephemerides"]
    object_types = request.get("object_types", {})

    if len(ephemerides) < 2:
        return {
            "conjunctions": [],
            "screening_summary": {
                "n_objects": len(ephemerides),
                "n_pairs_screened": 0,
                "n_conjunctions_flagged": 0,
                "coarse_step_s": None,
                "screening_radius_km": cfg["screening_radius_km"],
            },
        }

    object_ids = [e["object_id"] for e in ephemerides]
    if len(set(object_ids)) != len(object_ids):
        dupes = {oid for oid in object_ids if object_ids.count(oid) > 1}
        raise CollisionDetectionError(f"duplicate object_id(s) in ephemerides input: {dupes}")

    for e in ephemerides:
        frame = e.get("frame")
        if frame != "ECI_J2000":
            raise CollisionDetectionError(
                f"object '{e.get('object_id')}' has frame='{frame}', expected 'ECI_J2000'. "
                "Collision Detection Agent assumes the Prediction Agent has already converted "
                "TEME -> ECI before handoff; refusing to compute distances in a mismatched frame."
            )

    epoch = _parse_iso(window_spec["start_utc"])
    window_end_s = _to_seconds(window_spec["end_utc"], epoch)
    window = (0.0, window_end_s)

    # ---- build interpolants ----
    interpolants = []
    bounds_list = []
    for e in ephemerides:
        r_interp, v_interp, bounds = _build_interpolants(e["ephemeris"], epoch)
        interpolants.append((r_interp, v_interp))
        bounds_list.append(bounds)

    # ---- coarse KD-tree screen (native cadence; see ALIASING NOTE above _coarse_screen) ----
    candidates = _coarse_screen(
        ephemerides, epoch, cfg["screening_radius_km"], cfg["pad_for_aliasing"], cfg["max_rel_vel_kms"]
    )

    n_objects = len(ephemerides)
    n_pairs_screened = n_objects * (n_objects - 1) // 2

    # ---- primary/secondary convention ----
    def sort_key(oid: str) -> tuple:
        obj_type = object_types.get(oid, "")
        type_rank = {"PAYLOAD": 0}.get(obj_type, 1)  # PAYLOAD preferred as primary
        return (type_rank, oid)

    # ---- cluster + refine ----
    conjunctions = []
    for (i, j), flagged_times in candidates.items():
        windows = _cluster_into_windows(flagged_times, cfg["cluster_gap_s"])
        r_a, v_a = interpolants[i]
        r_b, v_b = interpolants[j]
        combined_bounds = (
            max(bounds_list[i][0], bounds_list[j][0]),
            min(bounds_list[i][1], bounds_list[j][1]),
        )
        for w in windows:
            result = _refine_tca(
                r_a, v_a, r_b, v_b, w, combined_bounds,
                cfg["fine_window_s"], cfg["fine_step_s"],
            )
            oid_i, oid_j = object_ids[i], object_ids[j]
            primary_id, secondary_id = sorted([oid_i, oid_j], key=sort_key)
            # if we swapped order relative to (i, j), flip the relative vectors
            if primary_id == oid_j:
                result["relative_position_km"] = [-x for x in result["relative_position_km"]]
                result["relative_velocity_vec_kms"] = [-x for x in result["relative_velocity_vec_kms"]]

            conjunctions.append({
                "primary_id": primary_id,
                "secondary_id": secondary_id,
                "tca_utc": _to_iso(result["tca_t_seconds"], epoch),
                "miss_distance_km": round(result["miss_distance_km"], 6),
                "relative_velocity_kms": round(result["relative_velocity_kms"], 6),
                "relative_position_km": [round(x, 6) for x in result["relative_position_km"]],
                "relative_velocity_vec_kms": [round(x, 6) for x in result["relative_velocity_vec_kms"]],
            })

    conjunctions.sort(key=lambda c: c["tca_utc"])

    return {
        "conjunctions": conjunctions,
        "screening_summary": {
            "n_objects": n_objects,
            "n_pairs_screened": n_pairs_screened,
            "n_conjunctions_flagged": len(conjunctions),
            "screening_radius_km": cfg["screening_radius_km"],
            "pad_for_aliasing": cfg["pad_for_aliasing"],
        },
    }


# --------------------------------------------------------------------------
# CLI entry point — run against a fixture/request JSON file without needing
# the API server or the Prediction Agent wired up.
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    import time

    parser = argparse.ArgumentParser(
        description="Run the Collision Detection Agent against a request JSON file."
    )
    parser.add_argument("input_file", help="Path to a JSON file matching the agent's request contract")
    parser.add_argument("--out", default=None, help="Write output JSON here instead of stdout")
    parser.add_argument("--screening-radius-km", type=float, default=None)
    parser.add_argument("--pad-for-aliasing", action="store_true")
    args = parser.parse_args()

    with open(args.input_file) as f:
        request = json.load(f)

    config = {}
    if args.screening_radius_km is not None:
        config["screening_radius_km"] = args.screening_radius_km
    if args.pad_for_aliasing:
        config["pad_for_aliasing"] = True

    t0 = time.time()
    result = run_collision_detection(request, config=config or None)
    elapsed_ms = (time.time() - t0) * 1000

    summary = result["screening_summary"]
    print(
        f"Screened {summary['n_pairs_screened']} possible pairs -> "
        f"{summary['n_conjunctions_flagged']} candidates flagged "
        f"({elapsed_ms:.1f} ms)",
        flush=True,
    )

    output_json = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(output_json)
        print(f"Wrote output to {args.out}")
    else:
        print(output_json)
