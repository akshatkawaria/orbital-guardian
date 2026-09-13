"""
Agent 9 core logic: two functions, each with a primary/fallback LLM
chain and a guardrail applied to the result.

    generate_report(decision, context)  -> (report_text, source, fidelity_ok)
    parse_constraints(operator_text)    -> (constraints_dict, source)

Both are provider-agnostic: swap LLM_PROVIDER in the environment and the
same functions keep working, all the way down to "mock" for zero-network
development. This is the module other agents/the Dashboard should import
-- llm_clients.py and guardrails.py are implementation details.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from . import guardrails
from .config import settings
from .llm_clients import LLMClient, LLMError, build_client
from .prompts import CONSTRAINT_SYSTEM_PROMPT, REPORT_SYSTEM_PROMPT
from .schemas import CONSTRAINT_JSON_SCHEMA

logger = logging.getLogger("agent9.explanation")

# Simple in-memory activity log the Dashboard's Agent Activity feed
# (Build Plan §10, GET /agent-log) can poll or subscribe to. Swap for a
# pub/sub or WebSocket push in production; kept trivial here so this
# module has zero infrastructure dependencies.
ACTIVITY_LOG: list[Dict[str, Any]] = []


def _log_event(event: str, **fields: Any) -> None:
    entry = {"agent": "explanation_llm_layer", "event": event, **fields}
    ACTIVITY_LOG.append(entry)
    logger.info(entry)


class ExplanationAgent:
    """
    Instantiate once (e.g. as a FastAPI dependency / module-level
    singleton). Holds a primary client and, unless LLM_PROVIDER is
    "mock", a fallback client, so a single rate-limit or timeout on the
    primary never surfaces as a pipeline failure.
    """

    def __init__(self, primary_provider: Optional[str] = None):
        provider = primary_provider or settings.llm_provider
        settings.validate_for_provider(provider)

        self.primary: LLMClient = build_client(provider)
        self.fallback: Optional[LLMClient] = None

        if provider == "gemini" and settings.groq_api_key:
            self.fallback = build_client("groq")
        elif provider == "groq" and settings.gemini_api_key:
            self.fallback = build_client("gemini")
        # provider == "mock" -> no fallback needed, nothing can fail

    # -----------------------------------------------------------------
    # Function A: report generation (Decision Agent JSON -> prose)
    # -----------------------------------------------------------------

    def generate_report(
        self, decision_payload: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, str, bool]:
        """
        Returns (report_text, source, numeric_fidelity_ok).
        `source` is one of "gemini" | "groq" | "mock" | "deterministic_fallback".
        """
        merged_input = {**decision_payload, **(context or {})}
        user_content = json.dumps(merged_input, indent=2)

        for attempt, client in enumerate(self._clients()):
            temperature = settings.temperature if attempt == 0 else settings.strict_retry_temperature
            try:
                text = client.generate_text(REPORT_SYSTEM_PROMPT, user_content, temperature)
            except LLMError as exc:
                _log_event("report_generation_failed", provider=client.name, error=str(exc))
                continue

            ok, unmatched = guardrails.numeric_fidelity_check(text, merged_input)
            if ok:
                _log_event("report_generated", provider=client.name, fidelity_ok=True)
                return text, client.name, True

            _log_event(
                "report_numeric_fidelity_failed",
                provider=client.name,
                unmatched_numbers=unmatched,
            )
            # One retry on the same client at temperature 0 before giving up on it
            try:
                retry_text = client.generate_text(
                    REPORT_SYSTEM_PROMPT, user_content, settings.strict_retry_temperature
                )
                ok2, unmatched2 = guardrails.numeric_fidelity_check(retry_text, merged_input)
                if ok2:
                    _log_event("report_generated_after_retry", provider=client.name)
                    return retry_text, client.name, True
                _log_event(
                    "report_retry_still_failed", provider=client.name, unmatched_numbers=unmatched2
                )
            except LLMError as exc:
                _log_event("report_retry_error", provider=client.name, error=str(exc))

        # Every provider failed or produced unverifiable numbers.
        _log_event("report_deterministic_fallback_used")
        return guardrails.deterministic_fallback_report(decision_payload), "deterministic_fallback", True

    # -----------------------------------------------------------------
    # Function B: constraint parsing (operator text -> Mission
    # Constraint Agent JSON, Build Plan §6/§9)
    # -----------------------------------------------------------------

    def parse_constraints(self, operator_text: str) -> Tuple[Dict[str, Any], str]:
        """
        Returns (constraints_dict, source). Raises ValueError if no
        provider can produce a schema-valid result -- callers should
        surface this as an HTTP 422 and ask the operator to rephrase,
        rather than silently forwarding a guessed constraint set to the
        Mission Constraint Agent.
        """
        last_error: Optional[Exception] = None

        for client in self._clients():
            try:
                raw = client.generate_json(
                    CONSTRAINT_SYSTEM_PROMPT,
                    operator_text,
                    CONSTRAINT_JSON_SCHEMA,
                    settings.temperature,
                )
                guardrails.validate_constraints(raw)
                _log_event("constraints_parsed", provider=client.name, constraints=raw)
                return raw, client.name
            except LLMError as exc:
                _log_event("constraint_parse_llm_error", provider=client.name, error=str(exc))
                last_error = exc
            except Exception as exc:  # jsonschema.ValidationError, JSONDecodeError, etc.
                _log_event("constraint_parse_invalid_schema", provider=client.name, error=str(exc))
                last_error = exc

        raise ValueError(
            f"Could not parse a valid constraint object from operator text. Last error: {last_error}"
        )

    def _clients(self):
        yield self.primary
        if self.fallback is not None:
            yield self.fallback
