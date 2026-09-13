"""
Shared math helpers for the agent stubs.

IMPORTANT — read this before treating any agent's numbers as flight-quality:
This module implements a plain two-body Keplerian propagator, not SGP4/SDP4.
It is a deliberate, clearly-labeled stand-in so the orchestrator has a real,
self-consistent set of orbits to push through the whole pipeline end-to-end
without depending on a compiled SGP4 package. Swapping in the real Prediction
Agent (build plan §2: SGP4 + mandatory TEME->ECI conversion, e.g. via the
`sgp4` or `skyfield` package) only requires replacing `propagate_mean_elements`
below with a call to that library -- every downstream agent (collision
detection onward) only ever sees the resulting r_km/v_kms arrays and does not
care how they were produced.

Similarly, the Risk Assessment Agent's Pc computation below does a genuine
numerical double-integration of the 2D Gaussian encounter-plane model (the
same integral Chan's and Foster's closed forms approximate) -- so Pc values
here are real, just computed by direct quadrature rather than the faster
closed-form series. The Maneuver Agent's cost-to-maneuver search is the one
piece that uses a frankly simplified geometric heuristic rather than a full
CW-to-encounter-plane re-projection; see the comment in maneuver.py for why,
and what a rigorous replacement would look like.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone, timedelta

import numpy as np

from app.config import MU_EARTH_KM3_S2


def parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rotation_perifocal_to_eci(raan_rad: float, inc_rad: float, argp_rad: float) -> np.ndarray:
    """Classical orbital elements -> ECI rotation matrix (3-1-3 sequence)."""
    cO, sO = math.cos(raan_rad), math.sin(raan_rad)
    ci, si = math.cos(inc_rad), math.sin(inc_rad)
    cw, sw = math.cos(argp_rad), math.sin(argp_rad)
    return np.array([
        [cO * cw - sO * sw * ci, -cO * sw - sO * cw * ci, sO * si],
        [sO * cw + cO * sw * ci, -sO * sw + cO * cw * ci, -cO * si],
        [sw * si, cw * si, ci],
    ])


def _solve_kepler(M: float, e: float, tol: float = 1e-10, max_iter: int = 50) -> float:
    """Newton-Raphson solve of Kepler's equation M = E - e*sin(E)."""
    E = M if e < 0.8 else math.pi
    for _ in range(max_iter):
        f = E - e * math.sin(E) - M
        fp = 1 - e * math.cos(E)
        dE = f / fp
        E -= dE
        if abs(dE) < tol:
            break
    return E


def elements_to_state(mean_elements: dict, t_since_epoch_s: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Two-body Keplerian propagation from a Tracking Agent mean_elements record.
    Returns (r_km, v_kms) in a pseudo-inertial ECI-like frame.
    """
    inc = math.radians(mean_elements["inclination_deg"])
    raan = math.radians(mean_elements["raan_deg"])
    e = mean_elements["eccentricity"]
    argp = math.radians(mean_elements["arg_perigee_deg"])
    M0 = math.radians(mean_elements["mean_anomaly_deg"])
    n_rev_day = mean_elements["mean_motion_rev_day"]

    n = n_rev_day * 2 * math.pi / 86400.0          # rad/s
    a = (MU_EARTH_KM3_S2 / (n ** 2)) ** (1.0 / 3.0)  # km

    M = M0 + n * t_since_epoch_s
    M = M % (2 * math.pi)
    E = _solve_kepler(M, e)

    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))
    r = a * (1 - e * math.cos(E))

    r_pf = np.array([r * math.cos(nu), r * math.sin(nu), 0.0])
    p = a * (1 - e ** 2)
    h = math.sqrt(MU_EARTH_KM3_S2 * max(p, 1e-9))
    v_pf = np.array([
        -MU_EARTH_KM3_S2 / h * math.sin(nu),
        MU_EARTH_KM3_S2 / h * (e + math.cos(nu)),
        0.0,
    ])

    R = _rotation_perifocal_to_eci(raan, inc, argp)
    return R @ r_pf, R @ v_pf


def eci_to_lat_lon_alt(r_km: np.ndarray, t_utc: datetime) -> tuple[float, float, float]:
    """Approximate ECI -> ECEF -> geodetic lat/lon/alt, for the ground-track view only."""
    jd = 2451545.0 + (t_utc - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
    T = (jd - 2451545.0) / 36525.0
    gmst_deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
                + 0.000387933 * T ** 2 - (T ** 3) / 38710000.0) % 360.0
    theta = math.radians(gmst_deg)
    Rz = np.array([[math.cos(theta), math.sin(theta), 0],
                   [-math.sin(theta), math.cos(theta), 0],
                   [0, 0, 1]])
    r_ecef = Rz @ r_km
    x, y, z = r_ecef
    r_mag = np.linalg.norm(r_ecef)
    lat = math.degrees(math.asin(z / r_mag))
    lon = math.degrees(math.atan2(y, x))
    from app.config import EARTH_RADIUS_KM
    alt = r_mag - EARTH_RADIUS_KM
    return lat, lon, alt


def mean_motion_rad_s(mean_elements: dict) -> float:
    return mean_elements["mean_motion_rev_day"] * 2 * math.pi / 86400.0
