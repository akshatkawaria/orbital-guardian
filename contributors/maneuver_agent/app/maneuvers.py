"""
Maneuver candidate generation.

For every (direction, delta_v) pair requested, this module:
  1. Propagates the satellite (unperturbed) from reference_time to burn_time.
  2. Builds the satellite's local RTN frame at burn_time.
  3. Applies the impulsive delta-v along the requested direction.
  4. Propagates the post-burn satellite state from burn_time to the
     predicted closest-approach time (TCA).
  5. Propagates the debris (unperturbed, no burn) from reference_time to TCA.
  6. Computes the Euclidean separation between the two at TCA.

Debris and the un-maneuvered satellite baseline are propagated once and
reused across all candidates for efficiency and to guarantee identical
"no burn" conditions are compared against for every candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from typing import List

import numpy as np

from .frame import build_local_frame, direction_unit_vector
from .models import ManeuverRequest
from .propagation import propagate_two_body

M_S_TO_KM_S = 1.0 / 1000.0


@dataclass
class RawCandidate:
    direction: str
    delta_v_m_s: float
    burn_time: datetime
    projected_miss_distance_km: float
    improvement_km: float
    meets_target: bool


@dataclass
class GenerationResult:
    candidates: List[RawCandidate]
    computed_baseline_miss_distance_km: float


def generate_candidates(req: ManeuverRequest) -> GenerationResult:
    r_sat0 = np.array(req.satellite_state.position_km, dtype=float)
    v_sat0 = np.array(req.satellite_state.velocity_km_s, dtype=float)
    r_deb0 = np.array(req.debris_state.position_km, dtype=float)
    v_deb0 = np.array(req.debris_state.velocity_km_s, dtype=float)

    t_burn_s = req.burn_lead_time_s
    t_tca_s = req.time_to_closest_approach_s
    dt_after_burn_s = t_tca_s - t_burn_s  # validated > 0 at model level

    burn_time = req.reference_time + timedelta(seconds=t_burn_s)

    # Debris follows an unperturbed path regardless of the satellite's
    # maneuver; propagate it once to TCA and reuse for every candidate.
    r_deb_tca, _ = propagate_two_body(r_deb0, v_deb0, t_tca_s)

    # Satellite's un-maneuvered ("do nothing") state at TCA, used to
    # sanity-check the caller-provided baseline_miss_distance_km and as
    # the reference point for improvement_km.
    r_sat_tca_nomanuever, _ = propagate_two_body(r_sat0, v_sat0, t_tca_s)
    computed_baseline_km = float(np.linalg.norm(r_sat_tca_nomanuever - r_deb_tca))

    # Satellite state at burn time (still unperturbed up to this point);
    # reused as the common starting point for every candidate burn.
    r_sat_burn, v_sat_burn = propagate_two_body(r_sat0, v_sat0, t_burn_s)
    frame = build_local_frame(r_sat_burn, v_sat_burn)

    candidates: List[RawCandidate] = []
    for direction, dv_m_s in product(req.allowed_directions, req.delta_v_options_m_s):
        dv_km_s = dv_m_s * M_S_TO_KM_S
        unit = direction_unit_vector(frame, direction)
        v_after_burn = v_sat_burn + dv_km_s * unit

        r_sat_tca, _ = propagate_two_body(r_sat_burn, v_after_burn, dt_after_burn_s)

        miss_km = float(np.linalg.norm(r_sat_tca - r_deb_tca))
        improvement_km = miss_km - computed_baseline_km
        meets_target = miss_km >= req.target_miss_distance_km

        candidates.append(
            RawCandidate(
                direction=direction,
                delta_v_m_s=dv_m_s,
                burn_time=burn_time,
                projected_miss_distance_km=miss_km,
                improvement_km=improvement_km,
                meets_target=meets_target,
            )
        )

    return GenerationResult(
        candidates=candidates, computed_baseline_miss_distance_km=computed_baseline_km
    )
