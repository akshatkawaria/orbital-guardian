"""
Deterministic orbital-mechanics helpers used by the Mission Constraint Agent's
checks. No ML, no LLM calls — every function here is closed-form or a short
numerical loop, per the build plan's "keep it deterministic and auditable"
directive for this agent.

Conventions
-----------
- Positions/velocities are plain (x, y, z) tuples/lists in km and km/s.
- LVLH (local-vertical-local-horizontal) frame axes, matching the build
  plan's Clohessy-Wiltshire formulation (§4.2 / §5):
    x = radial      (outward from Earth's center, along the primary's r vector)
    y = along-track (in the direction of the primary's velocity)
    z = cross-track (along the orbit's angular-momentum vector, r x v)
"""

from __future__ import annotations

import math
from typing import Literal, Sequence

EARTH_RADIUS_KM = 6378.137
MU_EARTH_KM3_S2 = 398600.4418  # matches Research Reference §4.3

Vec3 = Sequence[float]
Direction = Literal["along_track", "radial", "cross_track"]


def magnitude(v: Vec3) -> float:
    return math.sqrt(sum(c * c for c in v))


def distance(a: Vec3, b: Vec3) -> float:
    return magnitude([a[i] - b[i] for i in range(3)])


def dot(a: Vec3, b: Vec3) -> float:
    return sum(a[i] * b[i] for i in range(3))


def cross(a: Vec3, b: Vec3) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def normalize(v: Vec3) -> tuple[float, float, float]:
    m = magnitude(v)
    if m == 0:
        raise ValueError("cannot normalize a zero vector")
    return (v[0] / m, v[1] / m, v[2] / m)


# ---------------------------------------------------------------------------
# 1. Altitude-band check support
# ---------------------------------------------------------------------------

def delta_semi_major_axis_km(dv_ms: float, mean_motion_rad_s: float, direction: Direction) -> float:
    """
    First-order change in semi-major axis from a single impulsive burn on a
    near-circular orbit, from the Gauss variational equations:

        da/dt = (2 / n) * a_tangential      (tangential/along-track burns)

    Radial and cross-track burns change orbit orientation, not size, to
    first order, so their contribution here is treated as ~0 — the
    ground-region check (which cares about plane changes) is where those
    directions actually matter. See implementation-plan §2.2.
    """
    dv_kms = dv_ms / 1000.0
    if direction == "along_track":
        return (2.0 / mean_motion_rad_s) * dv_kms
    return 0.0


def post_maneuver_altitude_range(
    r_km: Vec3, mean_motion_rad_s: float, dv_ms: float, direction: Direction
) -> tuple[float, float]:
    """
    Returns a conservative (perigee_alt_km, apogee_alt_km) band estimate for
    the post-maneuver orbit, assuming a near-circular starting orbit. This is
    a fast screening approximation, not a substitute for re-propagating the
    selected candidate through SGP4 before it's actually executed.
    """
    a_current = magnitude(r_km)
    delta_a = delta_semi_major_axis_km(dv_ms, mean_motion_rad_s, direction)
    new_a = a_current + delta_a
    half_spread = abs(delta_a) * 0.5
    perigee_alt = new_a - EARTH_RADIUS_KM - half_spread
    apogee_alt = new_a - EARTH_RADIUS_KM + half_spread
    return perigee_alt, apogee_alt


# ---------------------------------------------------------------------------
# 2. Protected ground-region check support
# ---------------------------------------------------------------------------

def estimate_ground_track_shift_km(dv_ms: float, direction: Direction, horizon_hours: float) -> float:
    """
    Tier-1 heuristic (implementation-plan §2.3): along-track burns drift the
    ground track at ~1 km/day per 1 cm/s of Δv (Research Reference §4.1),
    because they change the semi-major axis and therefore the orbital
    period, producing a secular east/west ground-track walk.

    A single radial or cross-track impulse doesn't drive that same
    period-change mechanism to first order, so this fast linear rule has no
    magnitude to report for those directions (returns 0.0 here) — that is
    a known blind spot of this Tier-1 filter, not a claim that plane-change
    burns are risk-free. Any radial/cross-track candidate that survives
    Tier-1 near a protected ground region should still get a Tier-2 full
    re-propagation confirmation before being handed to the Decision Agent
    (see module docstring / implementation-plan §2.3).
    """
    if direction != "along_track":
        return 0.0
    dv_cms = dv_ms * 100.0  # m/s -> cm/s
    drift_km_per_day = dv_cms * 1.0
    return drift_km_per_day * (horizon_hours / 24.0)


# ---------------------------------------------------------------------------
# 3. Secondary-conjunction check support (Clohessy-Wiltshire propagation)
# ---------------------------------------------------------------------------

def lvlh_basis(r_km: Vec3, v_kms: Vec3) -> tuple[tuple[float, float, float], ...]:
    """Returns (radial_hat, along_track_hat, cross_track_hat) unit vectors."""
    radial_hat = normalize(r_km)
    h = cross(r_km, v_kms)  # specific angular momentum, r x v
    cross_track_hat = normalize(h)
    along_track_hat = cross(cross_track_hat, radial_hat)
    return radial_hat, along_track_hat, cross_track_hat


def eci_to_lvlh(vec_km: Vec3, r_km: Vec3, v_kms: Vec3) -> tuple[float, float, float]:
    radial_hat, along_track_hat, cross_track_hat = lvlh_basis(r_km, v_kms)
    return (dot(vec_km, radial_hat), dot(vec_km, along_track_hat), dot(vec_km, cross_track_hat))


def cw_offset_from_impulsive_burn(
    dv_ms: float, direction: Direction, mean_motion_rad_s: float, t_s: float
) -> tuple[float, float, float]:
    """
    Closed-form Clohessy-Wiltshire solution for the LVLH-frame position
    offset, at time t, between a trajectory that received an instantaneous
    Δv at t=0 and the unperturbed nominal trajectory (which stays at the
    LVLH origin by definition). Initial relative position is zero (a burn
    doesn't move the satellite instantaneously); initial relative velocity
    is the Δv itself, placed on the axis matching `direction`.

    Standard CW solution (Clohessy & Wiltshire, 1960; build plan §5):
        x(t) =  (sin(nt)/n) * xdot0                 + (2/n)(1 - cos(nt)) * ydot0
        y(t) = -(2/n)(1 - cos(nt)) * xdot0           + (1/n)(4 sin(nt) - 3nt) * ydot0
        z(t) =  (zdot0/n) * sin(nt)
    """
    n = mean_motion_rad_s
    dv_kms = dv_ms / 1000.0
    xdot0 = dv_kms if direction == "radial" else 0.0
    ydot0 = dv_kms if direction == "along_track" else 0.0
    zdot0 = dv_kms if direction == "cross_track" else 0.0

    nt = n * t_s
    sin_nt, cos_nt = math.sin(nt), math.cos(nt)

    x = (sin_nt / n) * xdot0 + (2.0 / n) * (1 - cos_nt) * ydot0
    y = -(2.0 / n) * (1 - cos_nt) * xdot0 + (1.0 / n) * (4 * sin_nt - 3 * nt) * ydot0
    z = (zdot0 / n) * sin_nt
    return (x, y, z)


def min_separation_to_fixed_object(
    primary_r_km: Vec3,
    primary_v_kms: Vec3,
    other_r_km: Vec3,
    dv_ms: float,
    direction: Direction,
    mean_motion_rad_s: float,
    screening_window_s: float,
    step_s: float,
) -> float:
    """
    Cheap screening-pass minimum separation (km) between the maneuvered
    primary and another object over the screening window, using the CW
    frame. Simplifying assumption (stated explicitly, matches
    implementation-plan §2.4): the other object's own relative drift over
    the short screening window is neglected, i.e. it's treated as
    stationary in the primary's original LVLH frame. That's an acceptable
    approximation for a fast *screening* pass whose job is to flag
    candidates for a full Risk Assessment re-check, not to certify a new
    collision probability.
    """
    other_offset_lvlh = eci_to_lvlh(
        [other_r_km[i] - primary_r_km[i] for i in range(3)], primary_r_km, primary_v_kms
    )

    min_sep = math.inf
    steps = max(1, int(screening_window_s // step_s))
    for i in range(steps + 1):
        t = i * step_s
        maneuvered_offset = cw_offset_from_impulsive_burn(dv_ms, direction, mean_motion_rad_s, t)
        sep = distance(other_offset_lvlh, maneuvered_offset)
        min_sep = min(min_sep, sep)
    return min_sep
