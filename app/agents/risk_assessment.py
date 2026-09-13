"""
Risk Assessment Agent (build plan §4) -- reference/stub implementation.

run(risk_input) -> RiskRecord dict, where risk_input matches the build
plan's §4 input contract exactly:
    primary_id, secondary_id, tca_utc, relative_position_km,
    relative_velocity_vec_kms, combined_hard_body_radius_m,
    position_covariance_primary_km2, position_covariance_secondary_km2

Unlike the tracking/prediction/maneuver stubs, this one is NOT a rough
approximation: it numerically evaluates the same 2D Gaussian encounter-plane
integral that Chan's and Foster's closed-form methods approximate for speed.
Specifically:
    1. project the combined position covariance onto the plane perpendicular
       to the relative velocity vector at TCA (the standard "encounter
       plane" construction used by every short-term-encounter Pc method),
    2. diagonalize that 2x2 covariance to remove any x/y correlation,
    3. integrate the resulting independent bivariate Gaussian over a disk of
       radius = combined hard-body radius, centered on the origin, with the
       Gaussian's mean at the (rotated) miss vector.
This is mathematically exact up to the quadrature tolerance -- it is simply
slower than a closed-form series, which is why real operational systems use
Chan's/Foster's approximations instead. For this reference build's scale
(tens of flagged conjunctions, not thousands), direct integration is the
right trade: correct answers with less room for a transcription error in a
closed-form series.
"""
from __future__ import annotations
import math
import numpy as np
from scipy import integrate

from app.agents.orbital_math import parse_iso
from app.config import PC_RED_THRESHOLD, PC_ORANGE_THRESHOLD, PC_YELLOW_THRESHOLD

_DEFAULT_COVARIANCE_KM2 = [[0.02, 0, 0], [0, 0.05, 0], [0, 0, 0.02]]


def _encounter_plane_basis(rel_vel_vec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = rel_vel_vec / np.linalg.norm(rel_vel_vec)
    # any vector not parallel to v
    ref = np.array([1.0, 0.0, 0.0]) if abs(v[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(v, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(v, e1)
    return e1, e2


def compute_pc(relative_position_km: list[float], relative_velocity_vec_kms: list[float],
                cov_primary_km2: list[list[float]], cov_secondary_km2: list[list[float]],
                combined_hard_body_radius_m: float) -> float:
    rel_pos = np.array(relative_position_km, dtype=float)
    rel_vel = np.array(relative_velocity_vec_kms, dtype=float)
    e1, e2 = _encounter_plane_basis(rel_vel)
    P = np.column_stack([e1, e2])  # (3,2)

    C = np.array(cov_primary_km2) + np.array(cov_secondary_km2)   # combined 3x3, km^2
    C2 = P.T @ C @ P                                               # 2x2 in encounter plane

    # diagonalize to remove x/y correlation
    eigvals, eigvecs = np.linalg.eigh(C2)
    sigma = np.sqrt(np.clip(eigvals, 1e-10, None))  # km

    miss_2d = P.T @ rel_pos                          # miss vector projected to plane
    miss_rot = eigvecs.T @ miss_2d                    # rotated into the diagonal (eigen) basis

    R_km = combined_hard_body_radius_m / 1000.0
    sx, sy = float(sigma[0]), float(sigma[1])
    mx, my = float(miss_rot[0]), float(miss_rot[1])

    def pdf(y, x):
        return (1.0 / (2 * math.pi * sx * sy)) * math.exp(
            -0.5 * (((x - mx) / sx) ** 2 + ((y - my) / sy) ** 2)
        )

    pc, _ = integrate.dblquad(
        pdf, -R_km, R_km,
        lambda x: -math.sqrt(max(R_km ** 2 - x ** 2, 0.0)),
        lambda x: math.sqrt(max(R_km ** 2 - x ** 2, 0.0)),
        epsabs=1e-14, epsrel=1e-8,
    )
    return max(pc, 0.0)


def _risk_tier(pc: float) -> str:
    if pc > PC_RED_THRESHOLD:
        return "RED"
    if pc > PC_ORANGE_THRESHOLD:
        return "ORANGE"
    if pc > PC_YELLOW_THRESHOLD:
        return "YELLOW"
    return "GREEN"


def run(risk_input: dict, now_utc=None) -> dict:
    from datetime import datetime, timezone
    now_utc = now_utc or datetime.now(timezone.utc)
    tca = parse_iso(risk_input["tca_utc"])

    cov_p = risk_input.get("position_covariance_primary_km2", _DEFAULT_COVARIANCE_KM2)
    cov_s = risk_input.get("position_covariance_secondary_km2", _DEFAULT_COVARIANCE_KM2)
    hbr_m = risk_input.get("combined_hard_body_radius_m", 15.0)

    pc = compute_pc(
        risk_input["relative_position_km"],
        risk_input["relative_velocity_vec_kms"],
        cov_p, cov_s, hbr_m,
    )

    return {
        "primary_id": risk_input["primary_id"],
        "secondary_id": risk_input["secondary_id"],
        "pc": pc,
        "pc_method": "2D-Gaussian-numerical-integration (Chan/Foster-equivalent)",
        "risk_tier": _risk_tier(pc),
        "time_to_tca_minutes": round((tca - now_utc).total_seconds() / 60.0, 2),
        "miss_distance_km": round(float(np.linalg.norm(risk_input["relative_position_km"])), 4),
    }
