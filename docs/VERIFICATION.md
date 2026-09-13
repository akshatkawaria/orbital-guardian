# Verification record

- Python integrated tests: **11 passed**, 2 dependency deprecation warnings.
- JavaScript syntax: passed `node --check static/app.js`.
- HTTP health and dashboard responses: 200.
- Operator parsing and optional negotiation requests: successful.
- Seeded calculation: 120 m initial miss; approximately 1.2 km after the selected
  0.4 m/s along-track maneuver under the documented demo model.
- External orbital feeds and real LLM providers: not exercised.
- Browser visual/interaction testing: **not completed**. No browser binary was
  installed in the execution environment; two browser setup attempts failed
  with a download timeout / installer lock error. The dashboard was served and
  syntax-checked, but no screenshot or successful browser interaction test is claimed.

Run `python -m pytest -q` from the project root to reproduce the automated checks.
