"""
FastAPI service for the Negotiation Agent (Module 8).

Route matches the Dashboard's API shape from the master build plan:
    POST /negotiate/{conjunction_id}   -> Negotiation Agent output
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from negotiation_agent import Proposal, negotiate, UnresolvableConjunction

app = FastAPI(title="Orbital Guardian — Negotiation Agent")

# In-memory agent activity log (matches Dashboard's /agent-log concept)
AGENT_LOG: list[dict] = []


class ProposalIn(BaseModel):
    object_id: str
    min_dv_ms_to_clear: Optional[float] = None
    fuel_remaining_pct: Optional[float] = None


class NegotiationRequest(BaseModel):
    conjunction_id: str
    proposals: list[ProposalIn]


@app.post("/negotiate/{conjunction_id}")
def negotiate_endpoint(conjunction_id: str, req: NegotiationRequest):
    if req.conjunction_id != conjunction_id:
        raise HTTPException(
            status_code=400,
            detail="conjunction_id in path and body must match",
        )

    proposals = [
        Proposal(
            object_id=p.object_id,
            min_dv_ms_to_clear=p.min_dv_ms_to_clear,
            fuel_remaining_pct=p.fuel_remaining_pct,
        )
        for p in req.proposals
    ]

    try:
        result = negotiate(conjunction_id, proposals)
    except UnresolvableConjunction as e:
        # Log as an escalation event rather than a silent 500
        AGENT_LOG.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "negotiation_agent",
            "event": "UNRESOLVED_ESCALATE",
            "conjunction_id": conjunction_id,
            "detail": str(e),
        })
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AGENT_LOG.append({
        "timestamp": result["decided_at_utc"],
        "agent": "negotiation_agent",
        "event": "MOVER_SELECTED",
        "conjunction_id": conjunction_id,
        "mover": result["mover"],
        "rationale": result["rationale"],
    })
    return result


@app.get("/agent-log")
def get_agent_log():
    return {"log": AGENT_LOG}


@app.get("/health")
def health():
    return {"status": "ok", "agent": "negotiation_agent"}
