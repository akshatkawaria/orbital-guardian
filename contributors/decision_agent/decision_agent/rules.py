"""
Deterministic decision rules for the Decision Agent.

Everything here is a pure function: same input -> same output, every time.
That's deliberate — this is the piece of the pipeline a judge/mentor should
be able to read top-to-bottom and verify by hand, per the build plan's
instruction to keep this agent "dead simple and legible."

No orbital mechanics, no ML, no LLM calls. This module only routes on
numbers that Agents 4 (Risk Assessment) and 6 (Mission Constraint) already
computed.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .models import EvaluatedCandidate

# --- Tunable constants -------------------------------------------------
# Kept as named, documented constants (not magic numbers) so every threshold
# is a single, citable, changeable line. Defaults are grounded in NASA CARA's
# published operational practice (see README §Grounding the thresholds).

# Below this many minutes to TCA, treat a YELLOW/ORANGE conjunction as one
# tier more urgent, since there may not be time for another screening cycle
# to catch a worsening trend before the encounter.
TIME_URGENCY_CUTOFF_MINUTES = 30.0

# Once MANEUVER_REQUIRED has fired for a conjunction, don't downgrade back to
# MONITOR/NO_ACTION unless Pc has dropped by at least this many orders of
# magnitude below the tier boundary. Prevents flapping between tiers as Pc
# estimates jitter slightly between screening cycles (see README §Hysteresis).
HYSTERESIS_ORDERS_OF_MAGNITUDE = 1.0

_TIER_ORDER = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


def _bump_tier(tier: str) -> str:
    """One notch more urgent, capped at RED."""
    order = list(_TIER_ORDER.keys())
    idx = min(_TIER_ORDER[tier] + 1, len(order) - 1)
    return order[idx]


def effective_risk_tier(risk_tier: str, time_to_tca_minutes: Optional[float]) -> str:
    """
    Apply the time-urgency rule (edge case 3.4d in the research doc):
    a YELLOW/ORANGE conjunction with little time left before TCA is treated
    as one notch more urgent than its raw Pc tier alone would suggest, since
    there may not be time for another screening cycle to catch a worsening
    trend.

    Deliberately does NOT apply to GREEN: genuinely low Pc means low risk
    regardless of how little time is left — time pressure should not
    manufacture risk that the probability model doesn't support. It also
    does not need to apply to RED, which is already the most urgent tier.
    """
    if risk_tier not in ("YELLOW", "ORANGE"):
        return risk_tier
    if time_to_tca_minutes is not None and time_to_tca_minutes < TIME_URGENCY_CUTOFF_MINUTES:
        return _bump_tier(risk_tier)
    return risk_tier


def select_maneuver(
    candidates: List[EvaluatedCandidate], pc_threshold_target: float
) -> Tuple[Optional[EvaluatedCandidate], bool, str]:
    """
    Implements the build-plan rule: "pick the lowest-Δv candidate among
    Mission-Agent-approved options that meets the Pc threshold."

    Returns (selected_candidate_or_None, meets_target, pool_used) where
    pool_used is "meets_threshold" | "best_approved_fallback" | "none".

    Deterministic tiebreak order (edge case 3.4b): lower dv_ms, then lower
    predicted_pc, then lower fuel_cost_pct, then maneuver_id lexical order —
    so identical input always produces identical output.
    """
    approved = [c for c in candidates if c.approved]
    if not approved:
        return None, False, "none"

    def sort_key(c: EvaluatedCandidate):
        return (
            c.dv_ms,
            c.predicted_pc,
            c.fuel_cost_pct if c.fuel_cost_pct is not None else float("inf"),
            c.maneuver_id,
        )

    meets_threshold = sorted(
        (c for c in approved if c.predicted_pc <= pc_threshold_target), key=sort_key
    )
    if meets_threshold:
        return meets_threshold[0], True, "meets_threshold"

    # Edge case 3.4c: nothing formally clears the target — fall back to the
    # best approved candidate rather than reporting a false "no option."
    best_approved = sorted(approved, key=sort_key)[0]
    return best_approved, False, "best_approved_fallback"


def confidence_pct(
    selected: EvaluatedCandidate,
    pc_threshold_target: float,
    time_to_tca_minutes: Optional[float],
    meets_target: bool,
) -> int:
    """
    A simple, legible composite confidence score (0-100) — not a rigorous
    statistical quantity. Two named, weighted factors:

      margin      — how far below the Pc target the selected candidate lands
                    (0 = right at the line, 1 = effectively zero residual risk)
      time_factor — how much lead time exists before TCA, saturating at 1hr
                    (more lead time -> more confidence there's room to verify
                    / re-screen before the encounter)

    If the candidate doesn't actually meet the target (fallback case),
    margin is forced to 0 rather than allowed to go negative-then-clamped,
    so confidence honestly reflects that this is a partial mitigation.
    """
    if not meets_target or pc_threshold_target <= 0:
        margin = 0.0
    else:
        margin = max(0.0, min(1.0, 1 - (selected.predicted_pc / pc_threshold_target)))

    if time_to_tca_minutes is None:
        time_factor = 0.5  # unknown lead time -> neutral, not zero
    else:
        time_factor = max(0.0, min(1.0, time_to_tca_minutes / 60.0))

    score = 0.7 * margin + 0.3 * time_factor
    return round(score * 100)


def apply_hysteresis(
    proposed_decision: str, previous_decision: Optional[str], pc: float, pc_threshold_target: float
) -> str:
    """
    Suppresses MANEUVER_REQUIRED -> MONITOR/NO_ACTION flapping when Pc is
    jittering right around a tier boundary between screening cycles
    (edge case 3.4e). Only relevant on the downgrade path; upgrades to a
    more urgent decision are never suppressed (safety-first).

    `previous_decision` is optional and supplied by the caller (e.g. the
    Dashboard/API layer tracking per-conjunction state) — this module stays
    a pure function and does not hold state itself.
    """
    urgency = {"NO_ACTION": 0, "MONITOR": 1, "MANEUVER_REQUIRED": 2, "ESCALATE_NO_VIABLE_OPTION": 2}

    if previous_decision is None:
        return proposed_decision

    is_downgrade = urgency.get(proposed_decision, 0) < urgency.get(previous_decision, 0)
    if not is_downgrade:
        return proposed_decision

    if previous_decision == "MANEUVER_REQUIRED":
        # Require Pc to have dropped a full order of magnitude below the
        # threshold that triggered the maneuver before allowing a downgrade.
        required_ceiling = pc_threshold_target * (10 ** -HYSTERESIS_ORDERS_OF_MAGNITUDE)
        if pc > required_ceiling:
            return previous_decision  # hold the prior, more urgent decision

    return proposed_decision
