"""
Prediction Agent (build plan §2) -- reference/stub implementation.

run(tracked_object, window_hours, step_seconds) -> EphemerisRecord dict

Uses plain two-body Keplerian propagation (see orbital_math.py's module
docstring for why, and what to swap in for production). Output shape matches
the build plan exactly: object_id, frame, and an ephemeris array of
{t_utc, r_km, v_kms} samples -- so the Collision Detection Agent (real or
stub) can't tell the difference from true SGP4 output.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from app.agents.orbital_math import elements_to_state, parse_iso, iso


def run(tracked_object: dict, window_hours: float = 24.0, step_seconds: int = 60) -> dict:
    epoch = parse_iso(tracked_object["epoch_utc"])
    n_steps = int((window_hours * 3600) // step_seconds) + 1
    points = []
    for k in range(n_steps):
        t = epoch + timedelta(seconds=k * step_seconds)
        dt_s = (t - epoch).total_seconds()
        r, v = elements_to_state(tracked_object["mean_elements"], dt_s)
        points.append({
            "t_utc": iso(t),
            "r_km": [round(float(x), 6) for x in r],
            "v_kms": [round(float(x), 6) for x in v],
        })
    return {
        "object_id": tracked_object["object_id"],
        "frame": "ECI_J2000",   # two-body propagation is frame-agnostic here; labeled to match
        "ephemeris": points,    # the real agent's contract (real SGP4 must convert TEME->ECI).
    }
