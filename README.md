# Orbital Guardian

Integrated multi-agent orbital sustainability hackathon demo with an interactive 3D mission console.

**Start with [EXECUTION_GUIDE.md](EXECUTION_GUIDE.md)** for Windows, macOS, and Linux installation, launch commands, API examples, team architecture, and troubleshooting.

Quick start (Python 3.11/3.12):

```bash
python -m venv .venv
# Activate .venv, or use its Python executable directly as shown in the guide.
python -m pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:8000 and click **Run simulation**.

All nine contributor agents are connected to the Module 10 orchestrator. Negotiation is optional via `/negotiate`; explanations use the mock provider by default. No API keys or frontend build are needed.

Run the integrated checks with `python -m pytest -q`.

See [integration notes](docs/INTEGRATION_NOTES.md) for fixes and modeling limits. The `original_archives/` folder preserves all ten supplied ZIP files unchanged.
