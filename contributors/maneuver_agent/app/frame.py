"""
Local orbital frame (RTN: Radial / Transverse / Normal) construction.

Given a state vector (r, v) in an inertial frame (e.g. ECI), this builds
the three orthonormal unit vectors used to define burn directions:

    R_hat (radial)     : points from Earth's center through the satellite.
                         "radial_out" = +R_hat, "radial_in" = -R_hat.
    N_hat (normal)      : orbit-normal, parallel to specific angular
                         momentum h = r x v. "normal" = +N_hat,
                         "anti_normal" = -N_hat.
    T_hat (transverse)  : completes the right-handed frame (T = N x R),
                         lying in the orbital plane, perpendicular to
                         R_hat, in the direction of motion.
                         "prograde" = +T_hat, "retrograde" = -T_hat.

Limitation (documented per project requirements): T_hat is defined via
the RTN construction (N x R), not by directly normalizing the velocity
vector. For a perfectly circular orbit these are identical. For a
near-circular LEO orbit (the case this demo targets) they are very close
approximations of each other; for a highly eccentric orbit they would
diverge and a velocity-aligned frame might be preferred instead. This
approximation is acceptable for the hackathon demo scope described in
the project brief.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LocalFrame:
    radial: np.ndarray      # R_hat, unit vector, shape (3,)
    transverse: np.ndarray  # T_hat, unit vector, shape (3,)
    normal: np.ndarray      # N_hat, unit vector, shape (3,)


# Unit vector for each supported burn direction, expressed as a function
# of the local frame. Kept as a simple lookup so main/maneuvers code stays
# declarative.
def direction_unit_vector(frame: LocalFrame, direction: str) -> np.ndarray:
    mapping = {
        "prograde": frame.transverse,
        "retrograde": -frame.transverse,
        "radial_out": frame.radial,
        "radial_in": -frame.radial,
        "normal": frame.normal,
        "anti_normal": -frame.normal,
    }
    if direction not in mapping:
        raise ValueError(f"Unsupported direction: {direction}")
    return mapping[direction]


def build_local_frame(r_km: np.ndarray, v_km_s: np.ndarray) -> LocalFrame:
    """Build the RTN frame from a position/velocity state vector.

    Raises ValueError if the vectors are too small/degenerate to define
    an orbital plane (caller is expected to have already validated this
    at the API boundary; this is a defense-in-depth check for any
    internally-propagated state, e.g. right before the burn).
    """
    r_mag = np.linalg.norm(r_km)
    if r_mag < 1e-9:
        raise ValueError("position vector magnitude is too small to define a radial direction")

    r_hat = r_km / r_mag

    h = np.cross(r_km, v_km_s)  # specific angular momentum
    h_mag = np.linalg.norm(h)
    if h_mag < 1e-9:
        raise ValueError(
            "position and velocity are (near-)parallel; cannot define an "
            "orbital plane / normal direction"
        )
    n_hat = h / h_mag

    t_hat = np.cross(n_hat, r_hat)  # already unit length since R,N orthonormal
    t_hat = t_hat / np.linalg.norm(t_hat)

    return LocalFrame(radial=r_hat, transverse=t_hat, normal=n_hat)
