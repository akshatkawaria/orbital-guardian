"""
Configuration for the Explanation / LLM Layer (Agent 9).

All knobs are environment-variable driven so this agent can be built,
tested, and deployed independently of the rest of the Orbital Guardian
pipeline, per the build plan's "JSON contract, not shared memory" rule.

LLM_PROVIDER controls which client is primary:
    "gemini"  -> Google Gemini (free tier) is primary, Groq is fallback
    "groq"    -> Groq is primary, Gemini is fallback
    "mock"    -> No network calls at all; deterministic canned responses.
                 Use this to build/demo/test the FastAPI routes, the
                 guardrails, and the Dashboard integration before you
                 have API keys, or when you're offline.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").lower()

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    # Extraction/report generation should be low-temperature: consistency
    # and repeatability matter more than creativity for this agent.
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    strict_retry_temperature: float = float(os.getenv("LLM_STRICT_RETRY_TEMPERATURE", "0.0"))

    request_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "12"))

    def validate_for_provider(self, provider: str) -> None:
        if provider == "gemini" and not self.gemini_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. "
                "Get a free key at https://aistudio.google.com/apikey"
            )
        if provider == "groq" and not self.groq_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set. "
                "Get a free key at https://console.groq.com/keys"
            )


settings = Settings()
