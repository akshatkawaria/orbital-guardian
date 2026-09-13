# Agent 9 — Explanation / LLM Layer

Turns the Decision Agent's structured JSON into a human-readable report, and
parses free-text operator instructions into the Mission Constraint Agent's
schema. **Never computes orbital mechanics** — every number in a report is
one the Decision Agent already produced; this layer only translates.

Fully working end-to-end with **zero API keys** via `LLM_PROVIDER=mock`, so
every other agent can integrate against it today. Swap in a real key later
with no code changes anywhere else.

---

## 1. Model choice, with reasoning

| | **Gemini 2.0 / 2.5 Flash (primary)** | **Groq — Llama 3.3 70B (fallback)** |
|---|---|---|
| Cost | Free, no credit card, no expiry (eligible countries) | Free, no credit card |
| Daily quota | ~1,500 requests/day, ~1M tokens/minute | ~1,000 requests/day, ~200K tokens/day |
| Structured output | **`response_schema` — decode-time schema enforcement**, not just prompting | JSON *mode* (valid JSON guaranteed, schema not enforced) |
| Reasoning quality | Strong enough for extraction + short summarization (this agent never needs deep multi-step reasoning — see "why this task doesn't need a frontier model" below) | Comparable for this task; excels at raw speed |
| Latency | Good | Best-in-class (LPU hardware, ~300+ tok/s) |

**Why Gemini is primary:** this agent's two jobs — templated summarization
and constrained field extraction — are exactly what `response_schema`
support is built for. It constrains *generation itself* to match your JSON
Schema, so `parse_constraints()` gets a structurally valid object far more
reliably than "please output JSON" prompting on any bare chat model. Its
free-tier request budget (~1,500/day) comfortably covers a live demo (tens
of calls) with huge headroom, and it needs no card and no phone
verification to get a key.

**Why Groq is the fallback, not a second primary:** if Gemini rate-limits
or times out mid-demo, Groq's sub-second LPU inference means the operator
chat box still feels instant, and its OpenAI-compatible endpoint means the
same code path (just no `response_schema`) works with one client swap. It
doesn't replace Gemini as primary because its daily token budget is
smaller and it lacks decode-time schema enforcement — every Groq JSON
response still gets validated in `guardrails.validate_constraints()`
before it's trusted.

**Why not a bigger/paid "reasoning" model:** report generation is
templated summarization of numbers you already trust, and constraint
parsing is closed-vocabulary field extraction (a handful of enum values
and named entities) — neither benefits from deep chain-of-thought or a
larger context window. A frontier reasoning model would add latency and
cost for zero quality gain on this specific job, and — per the
architecture's core discipline — this agent must *not* reason about
orbital mechanics anyway, so "more reasoning power" is the wrong axis to
optimize on here. If you later add the Kelvins-dataset Pc-trend predictor
mentioned in the research reference, *that's* a genuine ML task — but it's
a small classical/deep model trained on tabular CDM data, not an LLM call,
and it lives outside this agent.

**Free-tier numbers move often** — re-check
[ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits)
and [console.groq.com](https://console.groq.com) before your event. The
architecture (schema-constrained primary + fast fallback + local
re-validation) holds regardless of the exact quota figures.

---

## 2. Quickstart

```bash
cd agent9_explanation_llm
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

**Zero-key mode (build/demo immediately):**
```bash
# .env: LLM_PROVIDER=mock   (already the default)
uvicorn app.main:app --reload --port 8009
```

**With real LLMs:**
```bash
# .env:
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=<free key from https://aistudio.google.com/apikey>
# GROQ_API_KEY=<free key from https://console.groq.com/keys>   (optional but recommended fallback)
uvicorn app.main:app --reload --port 8009
```

Run the test suite (works fully offline against the mock provider):
```bash
pytest tests/ -v
```

Try it:
```bash
curl -X POST localhost:8009/decision/SAT-042/explain \
  -H "Content-Type: application/json" \
  -d @fixtures/decision_maneuver_required.json

curl -X POST localhost:8009/operator-command \
  -H "Content-Type: application/json" \
  -d '{"text": "Avoid this collision while minimizing fuel consumption and dont reduce the satellites communication coverage over India."}'
```

---

## 3. How other agents integrate with this one

This agent exposes exactly two data contracts — matching Build Plan §9 —
either of which can be called as an HTTP service or imported as a Python
module directly if you're running everything in one process.

### 3.1 As an HTTP service (recommended for independent development)

Run it standalone on its own port (`8009` above) and have Agent 10's
Dashboard/Orchestration API call it, or reverse-proxy it behind the same
gateway:

```
POST /decision/{primary_id}/explain
  body:  { "decision": <Decision Agent's exact output>, "context": {optional extras} }
  reply: { "report_text": "...", "source": "gemini|groq|mock|deterministic_fallback",
           "numeric_fidelity_ok": true }

POST /operator-command
  body:  { "text": "<free-text operator instruction>" }
  reply: { "constraints": { ...Mission Constraint Agent-shaped subset... },
           "source": "gemini|groq|mock", "raw_text": "..." }

GET  /agent-log     -> tail of this agent's activity events, for the
                        Dashboard's live Agent Activity feed (§10)
GET  /health        -> { status, primary_provider, fallback_provider }
```

Whoever owns Agent 7 (Decision Agent) needs to add exactly one outbound
call after producing its JSON: `POST` it to `/decision/{primary_id}/explain`
and forward the returned `report_text` to the Dashboard alongside the
decision itself.

Whoever owns Agent 6 (Mission Constraint Agent) needs to add exactly one
inbound path: accept `{"constraints": {...}}` from
`POST /operator-command`'s response and merge it into the constraint
object it already evaluates candidates against (Build Plan §6).

### 3.2 As a Python module (single-process / monorepo integration)

```python
from app.explanation_agent import ExplanationAgent

agent = ExplanationAgent()  # reads LLM_PROVIDER + keys from env

# Called from wherever Agent 7's decision object is produced:
report_text, source, fidelity_ok = agent.generate_report(
    decision_payload=decision_agent_output,      # exact §7 shape
    context=risk_and_collision_context,          # optional: secondary_id, risk_tier, etc.
)

# Called from wherever the operator chat box submits text:
constraints, source = agent.parse_constraints(operator_text)
# constraints is already shaped for Agent 6's `constraints` field --
# merge directly: mission_constraint_agent_input["constraints"].update(constraints)
```

`ExplanationAgent` is stateless aside from its two LLM client handles, so
it's safe to instantiate once at process startup and reuse across
requests/threads.

### 3.3 Fixture files for parallel development

Per the build plan's "swap for a stub" principle, `fixtures/` contains
ready-made Decision Agent outputs and operator commands so whoever is
building Agent 6, 7, or 10 can integrate against this agent's real
behavior (via `LLM_PROVIDER=mock`, no keys needed) before Agents 1–7's
actual math is wired up end-to-end.

---

## 4. What's enforced, and why it's safe to trust downstream

| Guardrail | Enforced in | Effect |
|---|---|---|
| Numeric fidelity check | `guardrails.numeric_fidelity_check` | Every number/timestamp in a generated report must trace back to the input JSON (within 2% rounding tolerance for numbers, exact match for timestamps). Object IDs like `SAT-042` are correctly excluded from being misread as numbers (`-42`). Failing this triggers a same-provider retry at temperature 0, then falls through to the next provider, then to a deterministic string-template report — never a silent hallucination. |
| Schema validation | `guardrails.validate_constraints` | Every parsed constraint object is checked against `CONSTRAINT_JSON_SCHEMA` before being returned — required even on the Gemini path (belt-and-suspenders) and the *only* real check on the Groq path (which lacks decode-time schema enforcement). |
| Fail-closed constraint parsing | `explanation_agent.parse_constraints` | If no provider produces a schema-valid result, the API returns `422` — it never forwards a guessed/partial constraint set to the Mission Constraint Agent. |
| Provider fallback | `ExplanationAgent._clients` | A single rate-limit/timeout on the primary provider is invisible to callers; only total failure of every configured provider triggers the deterministic fallback path. |
| Activity logging | `explanation_agent.ACTIVITY_LOG` | Every generation, retry, guardrail failure, and fallback is logged — feeds the Dashboard's Agent Activity stream (§10) and doubles as a debugging/regression trail. |

---

## 5. File map

```
app/
  config.py            settings from environment (.env)
  schemas.py            Pydantic models = the JSON contracts from Build Plan §9/§6/§7
  prompts.py             the two system prompts, shared across all providers
  llm_clients.py          GeminiClient / GroqClient / MockClient behind one interface
  guardrails.py            numeric-fidelity check, schema validation, deterministic fallback
  explanation_agent.py      orchestration: provider fallback chain + guardrails applied
  main.py                    FastAPI routes
fixtures/                 sample Decision Agent JSON + sample operator commands
tests/                     pytest suite, runs fully offline against the mock provider
```
