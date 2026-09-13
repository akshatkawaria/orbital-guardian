"""All runtime entries point to the uploaded team's code via adapters."""
from app.integration.adapters import REGISTRY
IS_REAL={name:True for name in REGISTRY}


def get(agent_name):
    return REGISTRY[agent_name]
