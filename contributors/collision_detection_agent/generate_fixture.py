"""
Synthetic fixture generator for the Collision Detection Agent.

Produces a JSON payload that matches the Prediction Agent's output contract
exactly (object_id, frame="ECI_J2000", ephemeris: [{t_utc, r_km, v_kms}]),
wrapped in the Collision Detection Agent's expected request shape
(screening_window + ephemerides). This lets you build, test, and demo the
Collision Detection Agent in complete isolation, before the Prediction
Agent is wired up -- exactly the point of the JSON-contract architecture.

Uses plain two-body circular-orbit propagation (NOT SGP4 -- that's the
Prediction Agent's job) purely to generate physically plausible LEO
trajectories with a few objects deliberately placed on a collision course.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

import numpy as np

MU_EARTH_KM3_S2 = 398600.4418  # standard gravitational parameter of Earth


def _rotation_matrix(inclination_rad: float, raan_rad: float) -> np.ndarray:
    """Rotate a planar (x,y,0) orbit into 3D via RAAN then inclination."""
    cO, sO = np.cos(raan_rad), np.sin(raan_rad)
    ci, si = np.cos(inclination_rad), np.sin(inclination_rad)
    Rz_raan = np.array([[cO, -sO, 0], [sO, cO, 0], [0, 0, 1]])
    Rx_inc = np.array([[1, 0, 0], [0, ci, -si], [0, si, ci]])
    return Rz_raan @ Rx_inc


def _circular_orbit_state(altitude_km: float, inclination_deg: float, raan_deg: float,
                           phase_deg: float, t_s: np.ndarray):
    """Return (r_km, v_kms) arrays of shape (len(t_s), 3) for a circular orbit."""
    r_mag = 6378.137 + altitude_km  # Earth mean radius + altitude
    n = np.sqrt(MU_EARTH_KM3_S2 / r_mag ** 3)  # mean motion, rad/s
    theta0 = np.radians(phase_deg)
    theta = theta0 + n * t_s

    x = r_mag * np.cos(theta)
    y = r_mag * np.sin(theta)
    z = np.zeros_like(theta)
    r_plane = np.stack([x, y, z], axis=-1)

    vx = -r_mag * n * np.sin(theta)
    vy = r_mag * n * np.cos(theta)
    vz = np.zeros_like(theta)
    v_plane = np.stack([vx, vy, vz], axis=-1)

    R = _rotation_matrix(np.radians(inclination_deg), np.radians(raan_deg))
    r = r_plane @ R.T
    v = v_plane @ R.T
    return r, v


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def generate_fixture(
    n_objects: int = 50,
    n_conjunctions: int = 3,
    window_hours: float = 24.0,
    step_s: float = 60.0,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    epoch = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)
    t_s = np.arange(0, window_hours * 3600 + step_s, step_s)

    ephemerides = []
    object_types = {}

    # background fleet: random LEO objects spread across altitude/inclination/RAAN
    n_background = max(0, n_objects - 2 * n_conjunctions)
    for k in range(n_background):
        object_id = f"OBJ-{k:04d}"
        altitude = np_rng.uniform(480, 620)
        inclination = np_rng.uniform(0, 100)
        raan = np_rng.uniform(0, 360)
        phase = np_rng.uniform(0, 360)
        r, v = _circular_orbit_state(altitude, inclination, raan, phase, t_s)
        ephemerides.append(_pack(object_id, epoch, t_s, r, v))
        object_types[object_id] = rng.choice(["PAYLOAD", "DEBRIS", "ROCKET_BODY"])

    # deliberately-conjuncting pairs: same inclination/RAAN/altitude, tiny phase offset
    # so the two objects pass very close to each other exactly once in the window.
    conjunction_pairs = []
    for c in range(n_conjunctions):
        altitude = np_rng.uniform(500, 560)
        inclination = np_rng.uniform(45, 98)
        raan = np_rng.uniform(0, 360)
        phase = np_rng.uniform(0, 360)

        primary_id = f"SAT-{c:03d}"
        secondary_id = f"DEB-{c:03d}"

        r1, v1 = _circular_orbit_state(altitude, inclination, raan, phase, t_s)
        # tiny phase offset -> a slow drift that crosses to ~0 separation once
        # within the window, then continues (guarantees exactly one close pass).
        phase_offset_deg = np_rng.uniform(-0.02, 0.02)
        r2, v2 = _circular_orbit_state(altitude, inclination, raan, phase + phase_offset_deg, t_s)

        ephemerides.append(_pack(primary_id, epoch, t_s, r1, v1))
        ephemerides.append(_pack(secondary_id, epoch, t_s, r2, v2))
        object_types[primary_id] = "PAYLOAD"
        object_types[secondary_id] = "DEBRIS"
        conjunction_pairs.append((primary_id, secondary_id))

    request = {
        "screening_window": {
            "start_utc": _iso(epoch),
            "end_utc": _iso(epoch + timedelta(hours=window_hours)),
        },
        "ephemerides": ephemerides,
        "object_types": object_types,
    }
    return request, conjunction_pairs


def _pack(object_id: str, epoch: datetime, t_s: np.ndarray, r: np.ndarray, v: np.ndarray) -> dict:
    return {
        "object_id": object_id,
        "frame": "ECI_J2000",
        "ephemeris": [
            {
                "t_utc": _iso(epoch + timedelta(seconds=float(t))),
                "r_km": [round(float(x), 6) for x in r[idx]],
                "v_kms": [round(float(x), 6) for x in v[idx]],
            }
            for idx, t in enumerate(t_s)
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-objects", type=int, default=50)
    parser.add_argument("--n-conjunctions", type=int, default=3)
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--step-s", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default="fixture_request.json")
    args = parser.parse_args()

    request, pairs = generate_fixture(
        args.n_objects, args.n_conjunctions, args.window_hours, args.step_s, args.seed
    )
    with open(args.out, "w") as f:
        json.dump(request, f, indent=2)

    print(f"Wrote {args.out}: {len(request['ephemerides'])} objects")
    print("Deliberately-conjuncting pairs (should appear in the agent's output):")
    for p in pairs:
        print(f"  {p[0]} <-> {p[1]}")
