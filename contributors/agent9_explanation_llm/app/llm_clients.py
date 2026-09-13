"""
LLM client adapters. Every provider exposes the same two methods so the
orchestration layer (explanation_agent.py) never has to know which
provider it's talking to:

    generate_text(system_prompt, user_content, temperature) -> str
    generate_json(system_prompt, user_content, schema, temperature) -> dict

Providers:
  - GeminiClient  : primary. Uses response_schema for controlled
                    generation (decode-time schema enforcement) --
                    see Google AI docs, "Generate structured output".
  - GroqClient    : fallback. OpenAI-compatible endpoint, fast LPU
                    inference. Uses response_format={"type":"json_object"}
                    (looser JSON-mode, not full schema enforcement) so we
                    always re-validate its output with jsonschema
                    ourselves in guardrails.py.
  - MockClient    : no network calls. Deterministic canned/templated
                    responses so the FastAPI routes, guardrails, and
                    Dashboard wiring can be built and demoed before any
                    API key exists, or in a network-restricted sandbox.

Free-tier notes (checked Sept 2026, re-verify before your event since
these move often):
  Gemini 2.0/2.5 Flash : ~15 RPM / ~1M TPM / ~1,500 requests-per-day,
                          no credit card in eligible countries.
  Groq free tier        : ~30 RPM, ~1,000 req/day, ~200K tokens/day
                          (per-model; check console.groq.com for current
                          numbers on whichever model you pick).
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict

from .config import settings


class LLMError(Exception):
    """Raised for any provider failure -- rate limit, timeout, bad response."""


class LLMClient(ABC):
    name: str

    @abstractmethod
    def generate_text(self, system_prompt: str, user_content: str, temperature: float) -> str:
        ...

    @abstractmethod
    def generate_json(
        self, system_prompt: str, user_content: str, schema: Dict[str, Any], temperature: float
    ) -> dict:
        ...


# ---------------------------------------------------------------------
# Gemini (primary)
# ---------------------------------------------------------------------

class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(self):
        # Imported lazily so the rest of the app works even if the
        # google-genai package isn't installed (e.g. in mock-only dev).
        from google import genai  # google-genai SDK

        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

    def generate_text(self, system_prompt: str, user_content: str, temperature: float) -> str:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_content,
                config={
                    "system_instruction": system_prompt,
                    "temperature": temperature,
                },
            )
            text = (response.text or "").strip()
            if not text:
                raise LLMError("Gemini returned empty text.")
            return text
        except Exception as exc:  # noqa: BLE001 -- normalize all provider errors
            raise LLMError(f"Gemini generate_text failed: {exc}") from exc

    def generate_json(
        self, system_prompt: str, user_content: str, schema: Dict[str, Any], temperature: float
    ) -> dict:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_content,
                config={
                    "system_instruction": system_prompt,
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
            return json.loads(response.text)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini generate_json failed: {exc}") from exc


# ---------------------------------------------------------------------
# Groq (fallback) -- OpenAI-compatible client
# ---------------------------------------------------------------------

class GroqClient(LLMClient):
    name = "groq"

    def __init__(self):
        from openai import OpenAI  # groq exposes an OpenAI-compatible API

        self._client = OpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )
        self._model = settings.groq_model

    def generate_text(self, system_prompt: str, user_content: str, temperature: float) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                temperature=temperature,
                timeout=settings.request_timeout_s,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            text = (completion.choices[0].message.content or "").strip()
            if not text:
                raise LLMError("Groq returned empty text.")
            return text
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Groq generate_text failed: {exc}") from exc

    def generate_json(
        self, system_prompt: str, user_content: str, schema: Dict[str, Any], temperature: float
    ) -> dict:
        # Groq's OpenAI-compatible endpoint supports JSON *mode* (valid
        # JSON guaranteed) but not full schema-constrained decoding like
        # Gemini. We push the schema into the prompt as guidance and
        # rely on guardrails.validate_constraints() downstream to catch
        # anything that slips through.
        schema_hint = json.dumps(schema, indent=2)
        augmented_system = (
            f"{system_prompt}\n\nRespond with ONLY a JSON object matching "
            f"this JSON Schema, no other text:\n{schema_hint}"
        )
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                temperature=temperature,
                timeout=settings.request_timeout_s,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": augmented_system},
                    {"role": "user", "content": user_content},
                ],
            )
            raw = completion.choices[0].message.content or "{}"
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Groq generate_json failed: {exc}") from exc


# ---------------------------------------------------------------------
# Mock (offline dev / demo without API keys / sandboxed CI)
# ---------------------------------------------------------------------

class MockClient(LLMClient):
    """
    Deterministic, template-based stand-in. Behaves closely enough to a
    real LLM (reads the input, produces varying but sensible output)
    that the rest of the pipeline can be built and tested against it,
    then swapped for GeminiClient/GroqClient with zero code changes
    elsewhere -- same "swap for a stub" principle the build plan uses
    for the orbital-mechanics agents.
    """

    name = "mock"

    def generate_text(self, system_prompt: str, user_content: str, temperature: float) -> str:
        time.sleep(0.05)  # simulate latency so UI loading states are exercised
        try:
            payload = json.loads(user_content)
        except json.JSONDecodeError:
            return "Unable to summarize: input was not valid JSON."

        decision = payload.get("decision", "UNKNOWN")
        primary = payload.get("primary_id", "the satellite")
        secondary = payload.get("secondary_id")
        maneuver = payload.get("selected_maneuver")

        if decision == "NO_ACTION":
            return (
                f"{primary} shows no significant collision risk at this time; "
                f"no maneuver is required and the object remains under routine monitoring."
            )
        if decision == "MONITOR":
            return (
                f"{primary} has an elevated but sub-actionable collision risk"
                f"{' with ' + secondary if secondary else ''}; "
                f"the system will continue monitoring and will re-evaluate as new tracking data arrives."
            )
        if decision == "MANEUVER_REQUIRED" and maneuver:
            vs = f" with {secondary}" if secondary else ""
            return (
                f"{primary} has a high collision risk{vs}"
                + (
                    f", closing to within {payload.get('miss_distance_km')} km in "
                    f"{payload.get('time_to_tca_minutes')} minutes"
                    if payload.get("miss_distance_km") is not None
                    else ""
                )
                + f". The recommended response is a {maneuver['dv_ms']} m/s {maneuver['direction']} "
                f"burn at {maneuver['burn_time_utc']}, which increases the miss distance to "
                f"{maneuver['predicted_miss_km']} km and costs {maneuver['fuel_cost_pct']}% of "
                f"remaining fuel."
            )
        return f"{primary}: decision status is {decision}."

    def generate_json(
        self, system_prompt: str, user_content: str, schema: Dict[str, Any], temperature: float
    ) -> dict:
        time.sleep(0.05)
        text = user_content.lower()

        # Very small deterministic "NLU" purely for offline demo purposes.
        # Real extraction happens via Gemini/Groq; this just has to be
        # good enough to exercise the pipeline end-to-end without a key.
        regions = []
        for country in [
            "india", "united states", "china", "brazil", "japan",
            "european union", "australia", "russia", "canada",
        ]:
            if country in text:
                regions.append(country.title() if country != "european union" else "European Union")

        optimize_for = "none"
        if "fuel" in text and ("minim" in text or "reduce" in text or "save" in text):
            optimize_for = "min_fuel"
        elif "miss distance" in text or "maxim" in text and "distance" in text:
            optimize_for = "min_miss_distance"
        elif "delta-v" in text or "dv" in text or "smallest" in text:
            optimize_for = "min_dv"

        max_fuel = None
        m = re.search(r"(\d+(\.\d+)?)\s*%.*fuel", text)
        if not m:
            m = re.search(r"fuel\D*?(\d+(\.\d+)?)\s*%", text)
        if m:
            max_fuel = float(m.group(1))

        altitude_band = None
        m = re.search(r"(\d{3,4})\s*(?:km)?\s*(?:to|-|and)\s*(\d{3,4})\s*km", text)
        if m:
            altitude_band = [float(m.group(1)), float(m.group(2))]

        return {
            "protected_ground_regions": regions,
            "optimize_for": optimize_for,
            "max_fuel_budget_pct_remaining": max_fuel,
            "altitude_band_km": altitude_band,
        }


def build_client(provider: str) -> LLMClient:
    if provider == "gemini":
        return GeminiClient()
    if provider == "groq":
        return GroqClient()
    if provider == "mock":
        return MockClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
