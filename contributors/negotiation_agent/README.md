# Negotiation Agent — Module 8 (Orbital Guardian)

Working implementation of the negotiation agent, per the build plan spec.

## Files
- `negotiation_agent.py` — core decision logic (pure Python, no dependencies)
- `test_negotiation_agent.py` — pytest suite, 11 tests covering every case in the spec
- `api.py` — FastAPI wrapper exposing `POST /negotiate/{conjunction_id}` + `GET /agent-log` + `GET /health`

## Run tests
```
pip install pytest --break-system-packages
pytest test_negotiation_agent.py -v
```

## Run the API
```
pip install fastapi uvicorn --break-system-packages
python3 -m uvicorn api:app --host 0.0.0.0 --port 8811
```

## Example call
```
curl -X POST http://localhost:8811/negotiate/SAT-042_vs_SAT-107 \
  -H "Content-Type: application/json" \
  -d '{
    "conjunction_id": "SAT-042_vs_SAT-107",
    "proposals": [
      {"object_id": "SAT-042", "min_dv_ms_to_clear": 0.7, "fuel_remaining_pct": 62},
      {"object_id": "SAT-107", "min_dv_ms_to_clear": 0.4, "fuel_remaining_pct": 18}
    ]
  }'
```
Expected: SAT-107 selected as mover, rationale "lower_dv_required".

## Wiring into the pipeline
Feed this the two Decision Agent (Module 7) outputs for the same `conjunction_id`
whenever BOTH sides report `decision: "MANEUVER_REQUIRED"`. Its output feeds the
Explanation/LLM Layer (Module 9) for the human-readable sentence, and the
Dashboard (Module 10) for the before/after panel + agent activity log.
