"""
Tracking Agent (build plan §1) -- reference/stub implementation.

run(mode, object_count, epoch_utc, guaranteed_conjunctions) -> list[TrackedObject dict]

Real mode (CelesTrak/Space-Track TLE ingest) is intentionally left as a thin
`fetch_and_parse_tle` stub below: this orchestrator does not have outbound
network access in every deployment environment (e.g. sandboxed grading), so
`run(mode="celestrak", ...)` raises NotImplementedError with a clear message
rather than silently returning fake data under a "real" label. Demo mode
(the synthetic fleet generator) is fully implemented and is what the build
plan recommends for the hackathon anyway (§1: "20-100 objects ... a few are
guaranteed to conjunct").
"""
from __future__ import annotations
import random
from datetime import datetime, timezone

from app.agents.orbital_math import iso


def _synthetic_fleet(object_count: int, epoch_utc: datetime, guaranteed_conjunctions: int) -> list[dict]:
    objects = []
    rng = random.Random(42)  # deterministic across refreshes, for reproducible demos

    n_debris_pairs = min(guaranteed_conjunctions, object_count // 4 or 1)

    # Base LEO shell: 500-600 km altitude payloads on varied planes.
    for i in range(object_count - n_debris_pairs):
        alt_km = rng.uniform(500, 600)
        a = 6378.137 + alt_km
        n_rev_day = 86400.0 / (2 * 3.14159265 * (a ** 1.5) / (398600.4418 ** 0.5))
        objects.append({
            "object_id": f"SAT-{i:03d}",
            "object_type": "PAYLOAD" if rng.random() > 0.15 else "ROCKET_BODY",
            "epoch_utc": iso(epoch_utc),
            "mean_elements": {
                "inclination_deg": round(rng.uniform(40, 98), 4),
                "raan_deg": round(rng.uniform(0, 360), 4),
                "eccentricity": round(rng.uniform(0.0001, 0.005), 7),
                "arg_perigee_deg": round(rng.uniform(0, 360), 4),
                "mean_anomaly_deg": round(rng.uniform(0, 360), 4),
                "mean_motion_rev_day": round(n_rev_day, 8),
                "bstar": round(rng.uniform(1e-5, 2e-4), 8),
            },
            "last_updated": iso(epoch_utc),
        })

    # Deliberately conjuncting debris: near-clone of an existing satellite's
    # elements with a small perturbation, so their orbital planes cross close
    # together within the propagation window (build plan §1 demo requirement).
    for j in range(n_debris_pairs):
        target = objects[j % len(objects)]
        me = dict(target["mean_elements"])
        # Perturbation scale is tuned so along-track/cross-track offsets land in the
        # few-hundred-meter range at TCA (comparable to typical covariance sigmas),
        # which is what actually drives Pc into YELLOW/ORANGE/RED rather than a
        # multi-km miss that's always GREEN regardless of covariance. 0.001 deg of
        # mean anomaly at LEO altitude ~ 6871 km * (0.001*pi/180) rad ~ 120 m.
        me["mean_anomaly_deg"] = round((me["mean_anomaly_deg"] + rng.uniform(-0.0015, 0.0015)) % 360, 6)
        me["raan_deg"] = round((me["raan_deg"] + rng.uniform(-0.0008, 0.0008)) % 360, 6)
        me["inclination_deg"] = round(me["inclination_deg"] + rng.uniform(-0.0008, 0.0008), 6)
        me["bstar"] = round(rng.uniform(1e-5, 5e-4), 8)
        objects.append({
            "object_id": f"DEB-{j:03d}",
            "object_type": "DEBRIS",
            "epoch_utc": iso(epoch_utc),
            "mean_elements": me,
            "last_updated": iso(epoch_utc),
        })

    return objects


def fetch_and_parse_tle(group: str | None) -> list[dict]:
    raise NotImplementedError(
        "Real TLE ingest (CelesTrak/Space-Track) is not wired up in this "
        "reference build -- register a real Tracking Agent via "
        "app.agents.registry.register('tracking', your_run_fn) that fetches "
        "and parses TLEs per NORAD Spacetrack Report #3."
    )


def run(mode: str = "synthetic", object_count: int = 50, group: str | None = None,
        guaranteed_conjunctions: int = 3, epoch_utc: datetime | None = None) -> list[dict]:
    epoch_utc = epoch_utc or datetime.now(timezone.utc)
    if mode == "synthetic":
        return _synthetic_fleet(object_count, epoch_utc, guaranteed_conjunctions)
    elif mode == "celestrak":
        return fetch_and_parse_tle(group)
    else:
        raise ValueError(f"Unknown tracking mode '{mode}'")
