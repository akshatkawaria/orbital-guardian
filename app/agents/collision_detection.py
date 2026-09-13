"""
Collision Detection Agent (build plan §3) -- reference/stub implementation.

run(ephemerides, screening_window) -> {"conjunctions": [...]}

Real agents would run the filter cascade described in the build plan
(apogee/perigee filter -> orbit-path filter -> time filter -> precise TCA
solve, or a KD-tree). At the object counts this reference build targets
(dozens to low hundreds), a fully vectorized brute-force pairwise scan over
the sampled ephemeris grid is fast enough and, importantly, is *correct*
rather than an approximation -- it just doesn't demonstrate the filter-
cascade performance story. A teammate implementing the real agent can drop
in the cascade/KD-tree version behind the exact same signature; nothing
downstream needs to change.

Assumes all ephemerides share the same time grid (same epoch/window/step),
which holds as long as /propagate was called once for the whole catalog --
exactly how the orchestrator's /simulate/start flow calls it.
"""
from __future__ import annotations
import numpy as np

from app.agents.orbital_math import parse_iso, iso
from app.config import SCREENING_MISS_DISTANCE_KM


def run(ephemerides: list[dict], screening_window: dict | None = None) -> dict:
    if len(ephemerides) < 2:
        return {"conjunctions": []}

    ids = [e["object_id"] for e in ephemerides]
    n_steps = min(len(e["ephemeris"]) for e in ephemerides)
    if n_steps < 3:
        return {"conjunctions": []}

    times = [parse_iso(p["t_utc"]) for p in ephemerides[0]["ephemeris"][:n_steps]]
    step_s = (times[1] - times[0]).total_seconds() if len(times) > 1 else 60.0

    positions = np.array([[p["r_km"] for p in e["ephemeris"][:n_steps]] for e in ephemerides])  # (N, T, 3)
    velocities = np.array([[p["v_kms"] for p in e["ephemeris"][:n_steps]] for e in ephemerides])

    conjunctions = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            diff = positions[i] - positions[j]            # (T, 3)
            dist = np.linalg.norm(diff, axis=1)            # (T,)
            k = int(np.argmin(dist))
            miss_km = float(dist[k])
            if miss_km > SCREENING_MISS_DISTANCE_KM:
                continue

            # Order pair so the primary is the payload/rocket body over debris
            # where possible (cosmetic only -- doesn't affect the math).
            primary_idx, secondary_idx = i, j
            if ids[i].startswith("DEB") and not ids[j].startswith("DEB"):
                primary_idx, secondary_idx = j, i

            rel_pos = (positions[secondary_idx][k] - positions[primary_idx][k]).tolist()
            rel_vel_vec = (velocities[secondary_idx][k] - velocities[primary_idx][k]).tolist()
            rel_vel_mag = float(np.linalg.norm(rel_vel_vec))

            conjunctions.append({
                "primary_id": ids[primary_idx],
                "secondary_id": ids[secondary_idx],
                "tca_utc": iso(times[k]),
                "miss_distance_km": round(miss_km, 4),
                "relative_velocity_kms": round(rel_vel_mag, 4),
                "relative_position_km": [round(x, 5) for x in rel_pos],
                "relative_velocity_vec_kms": [round(x, 5) for x in rel_vel_vec],
            })

    return {"conjunctions": conjunctions}
