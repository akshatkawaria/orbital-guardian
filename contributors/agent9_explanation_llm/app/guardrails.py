"""
Guardrails that enforce Agent 9's one hard rule: it narrates and
extracts, it never computes or invents orbital-mechanics numbers.

Two checks:
  1. numeric_fidelity_check -- every number quoted in a generated report
     must trace back to a number that was actually in the input JSON.
  2. validate_constraints    -- every parsed-constraint object must
     conform to the schema before it's allowed to reach the Mission
     Constraint Agent (belt-and-suspenders on top of Gemini's
     schema-constrained decoding, and the *only* real check when the
     Groq fallback path is used, since Groq's JSON mode doesn't
     enforce a schema at decode time).

Plus a deterministic, template-based fallback report generator so the
Dashboard never shows an error/blank state if every LLM provider is
unavailable mid-demo.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

import jsonschema

from .schemas import CONSTRAINT_JSON_SCHEMA

# Object/maneuver IDs like "SAT-042", "DEB-891", "M1" embed digits that
# are identifiers, not measurements -- e.g. "SAT-042" must never be read
# as the number -42. Strip these (and ISO-8601 timestamps, checked
# separately as opaque tokens below) before running number extraction.
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z]+-?\d+[A-Za-z0-9]*\b")
_ISO_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?")
_NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def _extract_numbers(text: str) -> List[float]:
    """
    Pull every *measurement* numeric literal out of a string, ignoring
    thousands separators, object/maneuver IDs (SAT-042, M1, ...), and
    ISO-8601 timestamps (handled as opaque tokens by _extract_iso_datetimes
    instead, since exploding "2026-09-12T13:46:00Z" into individual
    numbers would produce meaningless false positives/negatives).
    """
    cleaned = _ISO_DATETIME_RE.sub(" ", text)
    cleaned = _IDENTIFIER_RE.sub(" ", cleaned)
    out = []
    for match in _NUMBER_RE.findall(cleaned):
        try:
            out.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return out


def _extract_iso_datetimes(text: str) -> List[str]:
    return _ISO_DATETIME_RE.findall(text)


def _flatten_numbers(obj: Any, acc: List[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _flatten_numbers(v, acc)


def numeric_fidelity_check(
    report_text: str, input_payload: Dict[str, Any], rel_tol: float = 0.02
) -> Tuple[bool, List[float]]:
    """
    Returns (ok, unmatched_numbers).

    ok=True means:
      - every *measurement* number mentioned in report_text (object IDs
        like "SAT-042" and ISO timestamps are excluded from this pass --
        see below) is within rel_tol (default 2%, to allow for
        reasonable rounding like "0.72" -> "0.7") of some number that
        actually appeared in input_payload, AND
      - every ISO-8601 timestamp mentioned in report_text appears
        verbatim somewhere in input_payload (timestamps are opaque
        tokens: "close" doesn't mean anything for a burn time, so this
        is an exact-match check, not a tolerance check).

    Small integers that are extremely likely to be innocuous prose
    (0 and 1, e.g. "a single burn") are ignored in the numeric pass.
    """
    unmatched: List[Any] = []

    report_timestamps = _extract_iso_datetimes(report_text)
    if report_timestamps:
        source_dump = json.dumps(input_payload)
        for ts in report_timestamps:
            if ts not in source_dump:
                unmatched.append(ts)

    report_numbers = [n for n in _extract_numbers(report_text) if n not in (0.0, 1.0)]
    if report_numbers:
        source_numbers: List[float] = []
        _flatten_numbers(input_payload, source_numbers)
        for n in report_numbers:
            matched = any(
                abs(n - s) <= max(abs(s) * rel_tol, 1e-9) or abs(n - s) < 0.05
                for s in source_numbers
            )
            if not matched:
                unmatched.append(n)

    return (len(unmatched) == 0), unmatched


def validate_constraints(obj: Dict[str, Any]) -> None:
    """Raises jsonschema.ValidationError if obj doesn't match the constraint schema."""
    jsonschema.validate(instance=obj, schema=CONSTRAINT_JSON_SCHEMA)


def deterministic_fallback_report(decision_payload: Dict[str, Any]) -> str:
    """
    Last-resort report if every LLM provider fails. Pure string
    templating -- no LLM, no network, cannot hallucinate, always
    available. This is what keeps a total LLM outage from breaking the
    demo or the Dashboard.
    """
    primary = decision_payload.get("primary_id", "UNKNOWN")
    decision = decision_payload.get("decision", "UNKNOWN")
    maneuver = decision_payload.get("selected_maneuver")

    if decision == "MANEUVER_REQUIRED" and maneuver:
        return (
            f"{primary}: {decision}. Maneuver {maneuver.get('maneuver_id')} "
            f"({maneuver.get('direction')}, {maneuver.get('dv_ms')} m/s) at "
            f"{maneuver.get('burn_time_utc')} UTC. Predicted miss distance: "
            f"{maneuver.get('predicted_miss_km')} km. Fuel cost: "
            f"{maneuver.get('fuel_cost_pct')}%. "
            f"[Auto-generated fallback report -- LLM layer unavailable.]"
        )
    return (
        f"{primary}: {decision}. "
        f"[Auto-generated fallback report -- LLM layer unavailable.]"
    )
