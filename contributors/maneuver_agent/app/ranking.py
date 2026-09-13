"""
Deterministic ranking of maneuver candidates.

Rules (from the project brief), applied in order:
  1. Candidates meeting the target separation are ranked ahead of ones
     that don't.
  2. Among successful candidates, prefer lower delta-v.
  3. When delta-v is tied, prefer the greater miss distance.
  4. If none meet the target, the candidate with the largest improvement
     is recommended (and ranked first).
  5. Every candidate is returned (Agents 6 and 7 need the full set).

No randomness is used anywhere in this module; ties are broken solely
by the numeric rules above, so identical inputs always yield an
identical ordering.
"""
from __future__ import annotations

from typing import List, Tuple

from .maneuvers import RawCandidate
from .models import ManeuverCandidate

M_S_TO_KM_S = 1.0 / 1000.0


def _sort_key(c: RawCandidate) -> Tuple:
    if c.meets_target:
        # Successes first (0), then ascending delta-v, then descending
        # miss distance (so "-miss" sorts ascending -> largest miss first).
        return (0, c.delta_v_m_s, -c.projected_miss_distance_km)
    # Failures after all successes (1), best (largest) improvement first.
    return (1, -c.improvement_km, c.delta_v_m_s)


def _explain(c: RawCandidate, target_km: float) -> str:
    verb = "meets" if c.meets_target else "falls short of"
    return (
        f"A {c.delta_v_m_s:g} m/s {c.direction} burn at {c.burn_time.isoformat()} "
        f"is projected to change the miss distance to {c.projected_miss_distance_km:.3f} km "
        f"({c.improvement_km:+.3f} km vs. baseline), which {verb} the "
        f"{target_km:g} km target."
    )


def rank_candidates(
    raw_candidates: List[RawCandidate],
    max_delta_v_m_s: float,
    target_miss_distance_km: float,
) -> Tuple[List[ManeuverCandidate], str]:
    """Sort candidates per the ranking rules, assign ids/ranks, and return
    (ranked_candidates, recommended_candidate_id)."""
    ordered = sorted(raw_candidates, key=_sort_key)

    ranked: List[ManeuverCandidate] = []
    for i, c in enumerate(ordered, start=1):
        candidate_id = f"MNV-{i:03d}"
        fuel_cost_score = c.delta_v_m_s / max_delta_v_m_s if max_delta_v_m_s > 0 else 0.0
        ranked.append(
            ManeuverCandidate(
                candidate_id=candidate_id,
                direction=c.direction,
                delta_v_m_s=c.delta_v_m_s,
                burn_time=c.burn_time,
                projected_miss_distance_km=round(c.projected_miss_distance_km, 6),
                improvement_km=round(c.improvement_km, 6),
                meets_target=c.meets_target,
                fuel_cost_score=round(fuel_cost_score, 6),
                rank=i,
                explanation=_explain(c, target_miss_distance_km),
            )
        )

    recommended_id = ranked[0].candidate_id if ranked else None
    return ranked, recommended_id
