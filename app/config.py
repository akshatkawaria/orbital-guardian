"""
Shared constants for the whole orchestrator.

Nothing agent-specific lives here except the plain physical constants every
agent stub needs (mu, Earth radius) and the operational thresholds the build
plan calls out explicitly (Pc risk tiers). Keeping these in one place means
a real agent implementation and this stub implementation agree on the same
numbers without either having to import the other's internals.
"""

# --- Physical constants ---
MU_EARTH_KM3_S2 = 398600.4418     # Earth gravitational parameter
EARTH_RADIUS_KM = 6378.137

# --- Risk tiering (build plan §4: "Pc > 1e-4 -> RED per common operational practice") ---
PC_RED_THRESHOLD = 1e-4
PC_ORANGE_THRESHOLD = 1e-5
PC_YELLOW_THRESHOLD = 1e-6
# Pc below PC_YELLOW_THRESHOLD -> GREEN

# --- Collision screening ---
SCREENING_MISS_DISTANCE_KM = 25.0   # coarse filter: anything below this gets a full TCA refine

# --- Maneuver search ---
DEFAULT_PC_THRESHOLD_TARGET = 1e-6
MAX_DV_SEARCH_MS = 5.0              # give up if a direction needs more than this many m/s
DV_SEARCH_STEPS = 25

# --- Storage ---
import os
from pathlib import Path
DB_PATH = os.getenv("ORBITAL_DB_PATH", str(Path(__file__).resolve().parents[1] / "orbital_guardian.db"))
