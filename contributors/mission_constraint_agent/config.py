"""
Every threshold the checks use, in one place, with the reasoning for its
default written next to it. Override via MissionConstraintConfig(...) rather
than editing checks.py — keeps the checks themselves free of magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MissionConstraintConfig:
    # §2.3 ground-region check
    ground_region_tolerance_km: float = 10.0
    """Max tolerated ground-track shift over the coverage horizon before a
    protected-region violation is raised. Sized for *realistic* along-track
    nudges (single-digit mm/s to a few cm/s, per Research Reference §4.1) —
    at that scale, 10 km/day of drift tolerance is a sensible first cut.

    Heads up: the Maneuver Agent's own worked example in
    Orbital_Guardian_Build_Plan.md §5 uses illustrative candidates around
    ~0.7-1.2 m/s (70-120 cm/s) — the same order of magnitude the Research
    Reference explicitly calls out as "much smaller than the ~1 m/s
    figures sometimes used in illustrative examples." At that inflated
    scale, this default tolerance is unrealistically strict (any of those
    candidates would show 70+ km/day of heuristic drift). Use
    `MissionConstraintConfig.demo_scale()` below when working with
    illustrative-scale fixtures; keep this default for real mm/s-scale
    Maneuver Agent output."""

    ground_region_horizon_hours: float = 24.0
    """Near-term operational window: long enough to catch a meaningful
    along-track drift, short enough not to conflate with the maneuver
    being reversed after the conjunction passes (Research Reference §4.1).
    Radial/cross-track burns always score 0.0 under this Tier-1 rule
    (see orbital_math.estimate_ground_track_shift_km) — route those to a
    Tier-2 re-propagation check manually when a protected region is set."""

    # §2.4 secondary-conjunction check
    secondary_conjunction_danger_threshold_km: float = 5.0
    """Deliberately generous vs. a real Hard Body Radius — this is a
    *screening* threshold whose job is to flag "worth a Risk Assessment
    re-check", not to certify a new collision (implementation-plan §2.4)."""

    secondary_screening_window_s: float = 7200.0  # 2h: near-term window around the burn itself.
    """Deliberately shorter than the Collision Detection Agent's full 24h
    catalog screen — this check asks "does the burn itself immediately
    threaten another tracked object", not "will these two objects ever
    cross paths again". A long-horizon CW extrapolation accumulates
    unbounded secular drift (Research Reference §4.1) and would start
    flagging essentially any along-track burn as a false positive given
    enough time. Widen only if the mission profile calls for it."""

    secondary_screening_step_s: float = 60.0  # matches Prediction Agent's example step size

    @classmethod
    def demo_scale(cls) -> "MissionConstraintConfig":
        """
        A config preset sized for the build plan's own illustrative ~1 m/s
        candidate scale (see `demo_scenario.json`), rather than for
        realistic mm/s-to-cm/s operational burns. Widens the ground-region
        tolerance so illustrative-scale along-track candidates don't get
        flagged purely as an artifact of the schema's example numbers being
        ~50-100x larger than real single-digit-cm/s CAM burns. Swap back to
        the default constructor once the Maneuver Agent is producing
        realistic-scale Δv values.
        """
        return cls(ground_region_tolerance_km=100.0)
