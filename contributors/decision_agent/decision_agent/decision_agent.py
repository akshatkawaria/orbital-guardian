"""
DecisionAgent — the coordinator.

This is the only class other code should import. It:
  1. Parses/validates raw dicts into DecisionInput (via models.py)
  2. Applies the state machine + selection rule (via rules.py)
  3. Builds a template decision_reason string (deterministic, no LLM)
  4. Optionally applies hysteresis against a tracked previous decision
  5. Returns a plain JSON-serializable dict (via DecisionOutput.to_dict())

Usage:
    from decision_agent import DecisionAgent

    agent = DecisionAgent()
    output = agent.decide(input_dict)                     # stateless call
    output = agent.decide(input_dict, track_state=True)    # remembers last
                                                            # decision per
                                                            # conjunction_id
                                                            # for hysteresis
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Optional

from .models import DecisionInput, DecisionOutput, EvaluatedCandidate
from .rules import (
    effective_risk_tier,
    select_maneuver,
    confidence_pct as _confidence_pct,
    apply_hysteresis,
)


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DecisionAgent:
    """
    Stateless by default. Pass `track_state=True` to `decide()` if you want
    it to remember the last decision per `conjunction_id` in-process, which
    enables the hysteresis rule (see rules.apply_hysteresis). A dashboard/API
    layer running multiple worker processes should instead persist the last
    decision itself (e.g. in the same state DB the Tracking Agent uses) and
    pass it in via `previous_decision=` — `track_state` is a convenience for
    single-process demos, not a substitute for real persistence.
    """

    def __init__(self):
        self._last_decision_by_conjunction: Dict[str, str] = {}

    def decide(
        self,
        raw_input: Dict[str, Any],
        *,
        previous_decision: Optional[str] = None,
        track_state: bool = False,
    ) -> Dict[str, Any]:
        parsed = DecisionInput.from_dict(raw_input)

        if track_state and previous_decision is None and parsed.conjunction_id:
            previous_decision = self._last_decision_by_conjunction.get(parsed.conjunction_id)

        output = self._decide_parsed(parsed, previous_decision)

        if track_state and parsed.conjunction_id:
            self._last_decision_by_conjunction[parsed.conjunction_id] = output.decision

        return output.to_dict()

    # -- internal ---------------------------------------------------------

    def _decide_parsed(
        self, parsed: DecisionInput, previous_decision: Optional[str]
    ) -> DecisionOutput:
        tier = effective_risk_tier(parsed.risk_tier, parsed.time_to_tca_minutes)
        time_bumped = tier != parsed.risk_tier

        approved = [c for c in parsed.evaluated_candidates if c.approved]
        rejected = [c for c in parsed.evaluated_candidates if not c.approved]

        if tier == "GREEN":
            decision, reason, selected, meets_target, pool = (
                "NO_ACTION",
                f"risk_tier=GREEN (pc={parsed.pc:.2e}); no action required.",
                None,
                False,
                "n/a",
            )

        elif tier == "YELLOW":
            decision, selected, meets_target, pool = "MONITOR", None, False, "n/a"
            reason = f"risk_tier=YELLOW (pc={parsed.pc:.2e}); continue monitoring, re-screen next cycle."
            if time_bumped:
                reason += f" [escalated from {parsed.risk_tier}: time_to_tca={parsed.time_to_tca_minutes:.0f}min < {30:.0f}min urgency cutoff]"

        else:  # ORANGE or RED (post time-urgency bump)
            selected, meets_target, pool = select_maneuver(approved, parsed.pc_threshold_target)

            if selected is None:
                decision = "ESCALATE_NO_VIABLE_OPTION"
                reason = (
                    f"risk_tier={tier} (pc={parsed.pc:.2e}) but Mission Constraint Agent "
                    f"approved zero candidates ({len(rejected)} rejected) — human review required."
                )
            else:
                decision = "MANEUVER_REQUIRED"
                if meets_target:
                    reason = (
                        f"risk_tier={tier} (pc={parsed.pc:.2e}); selected {selected.maneuver_id} "
                        f"as lowest-Δv approved candidate meeting pc_threshold_target="
                        f"{parsed.pc_threshold_target:.1e}."
                    )
                else:
                    reason = (
                        f"risk_tier={tier} (pc={parsed.pc:.2e}); no approved candidate meets "
                        f"pc_threshold_target={parsed.pc_threshold_target:.1e}, so falling back to "
                        f"{selected.maneuver_id} as the best available approved option "
                        f"(predicted_pc={selected.predicted_pc:.1e}) — a follow-up maneuver may be "
                        f"needed next cycle."
                    )
            if time_bumped:
                reason += f" [escalated from {parsed.risk_tier} due to time_to_tca={parsed.time_to_tca_minutes:.0f}min]"

        # Hysteresis: only meaningful on (potential) downgrades, and only
        # ever applied by explicit caller opt-in via previous_decision.
        if previous_decision is not None:
            held_decision = apply_hysteresis(
                decision, previous_decision, parsed.pc, parsed.pc_threshold_target
            )
            if held_decision != decision:
                reason += (
                    f" [hysteresis: holding prior decision '{previous_decision}' — "
                    f"pc has not yet fallen a full order of magnitude below threshold]"
                )
                decision = held_decision

        conf = None
        selected_dict = None
        if decision == "MANEUVER_REQUIRED" and selected is not None:
            conf = _confidence_pct(
                selected, parsed.pc_threshold_target, parsed.time_to_tca_minutes, meets_target
            )
            selected_dict = selected.to_dict()
            selected_dict["meets_pc_target"] = meets_target

        return DecisionOutput(
            primary_id=parsed.primary_id,
            secondary_id=parsed.secondary_id,
            conjunction_id=parsed.conjunction_id,
            decision=decision,
            decision_reason=reason,
            decided_at_utc=_utc_now_iso(),
            selected_maneuver=selected_dict,
            rejected_candidates=[c.to_dict() for c in rejected],
            confidence_pct=conf,
        )
