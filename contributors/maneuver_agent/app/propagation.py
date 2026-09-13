"""
Simplified two-body numerical propagator.

Model: unperturbed Newtonian two-body gravity only.

    d(r)/dt = v
    d(v)/dt = -mu * r / |r|^3

No J2/oblateness, no atmospheric drag, no third-body (Sun/Moon) gravity,
and burns are treated as impulsive (instantaneous) velocity changes. This
is the standard simplification used for short-horizon (minutes-to-hours)
collision-avoidance demos; the project brief explicitly allows a
documented Clohessy-Wiltshire-class approximation for near-circular LEO,
and a direct numerical two-body integration (used here) is the more
accurate, still-simple alternative the brief lists as preferred.

Determinism: `scipy.integrate.solve_ivp` with a fixed high-order method
and tight fixed tolerances is used, so identical inputs always produce
identical outputs (required by the project's "no random behaviour" and
repeatability requirements).

Limitations (documented, per project requirements):
    * No perturbations (J2, drag, luni-solar, SRP) -> valid for short
      propagation windows (this demo assumes at most a few hours between
      reference time and encounter), not for multi-day predictions.
    * Impulsive burn model -> real thruster burns have finite duration;
      for the small (cm/s-to-dm/s) delta-v's used here the impulsive
      approximation is standard practice and the error is negligible.
    * Two-body only -> ignores debris/satellite non-gravitational
      accelerations (drag is most significant for low-mass debris at
      very low LEO altitudes over long windows; negligible here).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
from scipy.integrate import solve_ivp

# Earth's gravitational parameter, km^3/s^2 (standard value used across
# the reference material: Vallado 2013).
MU_EARTH_KM3_S2 = 398600.4418


def _two_body_eom(t: float, state: np.ndarray, mu: float) -> np.ndarray:
    r = state[0:3]
    v = state[3:6]
    r_mag = np.linalg.norm(r)
    a = -mu * r / r_mag**3
    return np.concatenate([v, a])


def propagate_two_body(
    r_km: np.ndarray, v_km_s: np.ndarray, dt_s: float, mu: float = MU_EARTH_KM3_S2
) -> Tuple[np.ndarray, np.ndarray]:
    """Propagate a two-body state forward (or backward) by dt_s seconds.

    Returns (r_new_km, v_new_km_s). dt_s == 0 returns the input state
    unchanged (no integration performed).
    """
    if dt_s == 0:
        return np.array(r_km, dtype=float), np.array(v_km_s, dtype=float)

    state0 = np.concatenate([np.asarray(r_km, dtype=float), np.asarray(v_km_s, dtype=float)])

    sol = solve_ivp(
        fun=_two_body_eom,
        t_span=(0.0, dt_s),
        y0=state0,
        method="DOP853",
        args=(mu,),
        rtol=1e-12,
        atol=1e-10,
        dense_output=False,
    )
    if not sol.success:
        raise RuntimeError(f"Two-body propagation failed: {sol.message}")

    final = sol.y[:, -1]
    return final[0:3], final[3:6]
