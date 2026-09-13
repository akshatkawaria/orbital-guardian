"""
chan_pc.py - Chan's closed-form probability-of-collision (Pc) approximation.

Reference: Chan, F. K. (2008), "Spacecraft Collision Probability", Aerospace
Press. Also see Alfriend et al. (1999), Space Debris 1(1):21-35.

This module ONLY computes Pc from geometry + covariance. It does not decide
risk tiers (that's risk_agent.py) and does not find conjunctions (Phase 3).
"""
import math
import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr


def combine_covariances(cov_primary: np.ndarray, cov_secondary: np.ndarray) -> np.ndarray:
    """Two independent trackers -> combined covariance is the sum."""
    return np.array(cov_primary) + np.array(cov_secondary)


def project_to_conjunction_plane(relative_position_km, relative_velocity_kms, combined_cov_km2):
    """
    Project the 3D relative position and combined covariance onto the 2D
    plane perpendicular to the relative velocity vector (the "B-plane" /
    conjunction plane). Motion along the velocity direction doesn't matter
    for collision -- what matters is where the two objects are relative to
    each other in the plane they cross at the moment of closest approach.

    Returns:
        (x0, z0): 2D miss-distance components in the conjunction plane (km)
        cov_2d: 2x2 covariance matrix in the conjunction plane (km^2)
    """
    r = np.array(relative_position_km, dtype=float)
    v = np.array(relative_velocity_kms, dtype=float)
    if not np.all(np.isfinite(r)) or not np.all(np.isfinite(v)) or np.linalg.norm(v) < 1e-10:
        raise ValueError("Finite geometry and nonzero relative velocity are required")
    v_hat = v / np.linalg.norm(v)

    # Build an orthonormal basis (x_hat, z_hat) spanning the plane
    # perpendicular to v_hat.
    arbitrary = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(arbitrary, v_hat)) > 0.9:
        arbitrary = np.array([0.0, 1.0, 0.0])
    x_hat = np.cross(v_hat, arbitrary)
    x_hat /= np.linalg.norm(x_hat)
    z_hat = np.cross(v_hat, x_hat)
    z_hat /= np.linalg.norm(z_hat)

    # Project the miss-distance vector onto the 2D plane basis.
    x0 = float(np.dot(r, x_hat))
    z0 = float(np.dot(r, z_hat))

    # Project the covariance matrix: rotate into (x_hat, v_hat, z_hat) frame,
    # then drop the along-velocity row/column (that axis doesn't affect Pc).
    rotation = np.vstack([x_hat, v_hat, z_hat])  # 3x3, rows are new basis vectors
    cov_rotated = rotation @ np.array(combined_cov_km2) @ rotation.T
    cov_2d = np.array([
        [cov_rotated[0, 0], cov_rotated[0, 2]],
        [cov_rotated[2, 0], cov_rotated[2, 2]],
    ])
    return x0, z0, cov_2d


def _chan_series(u: float, v: float, n_terms: int = 15) -> float:
    """
    Chan's closed-form series approximation:

        Pc = exp(-v/2) * sum_{m=0}^{N} [ u^(m+1) / (2^m * (m+1)!) ]
                                       * sum_{k=0}^{m} [ v^k / (2^k * k!) ]

    where u = R^2 / sigma^2 (combined hard-body radius scaled by the
    conjunction-plane sigma) and v is the squared Mahalanobis-style miss
    distance in the conjunction plane.
    """
    total = 0.0
    for m in range(n_terms):
        inner = sum((v ** k) / (2 ** k * math.factorial(k)) for k in range(m + 1))
        outer_term = (u ** (m + 1)) / (2 ** m * math.factorial(m + 1))
        total += outer_term * inner
    return math.exp(-v / 2.0) * total


def chan_pc(relative_position_km, relative_velocity_kms, combined_hard_body_radius_m,
            cov_primary_km2, cov_secondary_km2) -> dict:
    """
    Main entry point. Returns Pc plus the intermediate geometry, so the
    caller (risk_agent.py) can log/display it for the demo.
    """
    combined_cov = combine_covariances(cov_primary_km2, cov_secondary_km2)
    x0, z0, cov_2d = project_to_conjunction_plane(
        relative_position_km, relative_velocity_kms, combined_cov
    )

    # Diagonalize the 2D covariance to get principal-axis sigmas. Chan's
    # method handles the elliptical (sigma_x != sigma_z) case directly --
    # no circularization approximation needed.
    eigvals, eigvecs = np.linalg.eigh(cov_2d)
    if not np.all(np.isfinite(eigvals)) or np.any(eigvals <= 0):
        raise ValueError("Encounter-plane covariance must be positive definite")
    sigma_x, sigma_z = math.sqrt(eigvals[0]), math.sqrt(eigvals[1])

    # Rotate the miss-distance vector into the same principal-axis frame.
    miss_vec = np.array([x0, z0])
    rotated_miss = eigvecs.T @ miss_vec
    mx, mz = float(rotated_miss[0]), float(rotated_miss[1])

    hbr_km = combined_hard_body_radius_m / 1000.0

    # Exact elliptical form (Chan 2008):
    #   v = (x0/sigma_x)^2 + (z0/sigma_z)^2
    #   u = HBR^2 / (sigma_x * sigma_z)
    v = (mx / sigma_x) ** 2 + (mz / sigma_z) ** 2
    u = (hbr_km ** 2) / (sigma_x * sigma_z)

    # Integration fix: the supplied truncated series failed the centered
    # isotropic analytic case. Integrate the Gaussian over the hard-body disk
    # in principal axes instead; retain this public function for compatibility.
    if hbr_km < 0:
        raise ValueError("Hard-body radius must be nonnegative")
    def integrand(x):
        half_height = math.sqrt(max(0.0, hbr_km*hbr_km - x*x))
        density_x = math.exp(-0.5*((x-mx)/sigma_x)**2) / (math.sqrt(2*math.pi)*sigma_x)
        return density_x * (ndtr((half_height-mz)/sigma_z) - ndtr((-half_height-mz)/sigma_z))
    pc = quad(integrand, -hbr_km, hbr_km, epsabs=1e-12, limit=100)[0] if hbr_km else 0.0
    pc = min(max(pc, 0.0), 1.0)  # numerical guard

    return {
        "pc": pc,
        "miss_distance_km": float(np.linalg.norm(miss_vec)),
        "sigma_x_km": sigma_x,
        "sigma_z_km": sigma_z,
        "combined_hbr_km": hbr_km,
    }
