"""
Orbital Guardian — Decision Agent (Coordinator)

Public API:
    from decision_agent import DecisionAgent

    agent = DecisionAgent()
    output = agent.decide(input_dict)

`input_dict` and the returned dict are plain JSON-serializable structures —
see README.md for the full schema. No orbital-mechanics math happens in this
package; it only routes on numbers computed upstream (Risk Assessment +
Mission Constraint Agents), per the project's architectural rule that
decision logic stays deterministic and auditable.
"""

from .decision_agent import DecisionAgent
from .models import DecisionInput, DecisionOutput, EvaluatedCandidate

__all__ = ["DecisionAgent", "DecisionInput", "DecisionOutput", "EvaluatedCandidate"]
__version__ = "1.0.0"
