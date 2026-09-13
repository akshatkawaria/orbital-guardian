"""
Agent 9 -- Explanation / LLM Layer -- standalone FastAPI service.

Can run as its own microservice (recommended for independent
build/demo, per the build plan's parallel-development principle) and be
reverse-proxied behind the main Dashboard/Orchestration API (Agent 10),
or its router can be `include_router`-ed directly into that API's app.

Endpoints:
    POST /decision/{primary_id}/explain   -> report generation
    POST /operator-command                -> constraint parsing
    GET  /agent-log                       -> this agent's activity feed
    GET  /health                          -> liveness/readiness probe

Run locally:
    export LLM_PROVIDER=mock          # or gemini / groq, with the matching API key set
    uvicorn app.main:app --reload --port 8009
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .explanation_agent import ACTIVITY_LOG, ExplanationAgent
from .schemas import (
    ExplainRequest,
    ExplainResponse,
    OperatorCommandRequest,
    OperatorCommandResponse,
    ParsedConstraints,
)

app = FastAPI(
    title="Orbital Guardian -- Agent 9: Explanation / LLM Layer",
    description=(
        "Turns Decision Agent JSON into human-readable reports, and "
        "parses free-text operator constraints into the Mission "
        "Constraint Agent's schema. Never computes orbital mechanics."
    ),
    version="1.0.0",
)

# Single shared instance. ExplanationAgent reads LLM_PROVIDER (and the
# matching API key) from the environment at construction time -- see
# app/config.py.
agent = ExplanationAgent()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "primary_provider": agent.primary.name,
        "fallback_provider": agent.fallback.name if agent.fallback else None,
    }


@app.post("/decision/{primary_id}/explain", response_model=ExplainResponse)
def explain_decision(primary_id: str, body: ExplainRequest) -> ExplainResponse:
    """
    Report generation. `primary_id` in the path is validated against
    the body for consistency but the body's decision.primary_id is the
    source of truth (it's what actually came from the Decision Agent).
    """
    if body.decision.primary_id != primary_id:
        raise HTTPException(
            400,
            f"Path primary_id ({primary_id!r}) does not match "
            f"body.decision.primary_id ({body.decision.primary_id!r}).",
        )

    decision_payload = body.decision.model_dump()
    context_payload = body.context.model_dump(exclude_none=True) if body.context else {}

    report_text, source, fidelity_ok = agent.generate_report(decision_payload, context_payload)

    return ExplainResponse(
        report_text=report_text,
        source=source,  # type: ignore[arg-type]
        numeric_fidelity_ok=fidelity_ok,
    )


@app.post("/operator-command", response_model=OperatorCommandResponse)
def operator_command(body: OperatorCommandRequest) -> OperatorCommandResponse:
    """
    Constraint parsing. On failure to produce a schema-valid result
    from every configured provider, returns 422 rather than forwarding
    a guessed/partial constraint set downstream to the Mission
    Constraint Agent -- an operator should be asked to rephrase, not
    silently ignored or misinterpreted.
    """
    try:
        raw, source = agent.parse_constraints(body.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    return OperatorCommandResponse(
        constraints=ParsedConstraints(**raw),
        source=source,  # type: ignore[arg-type]
        raw_text=body.text,
    )


@app.get("/agent-log")
def agent_log(limit: int = 50) -> list[dict]:
    """Tail of this agent's activity log, for the Dashboard's live feed (Build Plan §10)."""
    return ACTIVITY_LOG[-limit:]
