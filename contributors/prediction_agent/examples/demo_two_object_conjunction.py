"""
Build-plan Phase 2 demo target:

    "Pick any 2 objects, show their two 24-hour ground tracks overlaid on a
    3D globe, and print the minimum separation over that window."

This script needs no other agent to exist yet -- it only depends on this
module. Run it and it will:

  1. Propagate two synthetic objects (a LEO satellite + a debris fragment
     on a deliberately close, slightly perturbed orbit) over a 24h window.
  2. Write each object's ephemeris to JSON, in the exact shape the
     Collision Detection Agent expects as input -- so these files double
     as fixture data for whoever builds that agent next, per the build
     plan's "swap for a stub" parallel-development model.
  3. Compute and print the minimum separation and the time it occurs --
     a one-pair preview of the TCA solve the Collision Detection Agent
     will eventually do at catalog scale.

Usage:
    python demo_two_object_conjunction.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prediction_agent.core import propagate
from prediction_agent.models import MeanElements, PredictionRequest

OUT_DIR = Path(__file__).resolve().parent / "output"


def min_separation(ephem_a: list[dict], ephem_b: list[dict]) -> tuple[float, str]:
    """
    Vectorized minimum-separation search over two aligned ephemerides.
    This is a single-pair preview of the Collision Detection Agent's
    eventual TCA solve (see Orbital_Guardian_Build_Plan.md §3): the same
    "distance(t) -> argmin" idea, just without the filter cascade that
    makes it tractable at catalog scale.
    """
    ra = np.array([p["r_km"] for p in ephem_a])
    rb = np.array([p["r_km"] for p in ephem_b])
    dist = np.linalg.norm(ra - rb, axis=1)
    i_min = int(np.argmin(dist))
    return float(dist[i_min]), ephem_a[i_min]["t_utc"]


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    window = dict(
        propagate_from_utc="2026-09-12T00:00:00Z",
        propagate_to_utc="2026-09-13T00:00:00Z",
        step_seconds=60,
    )

    # SAT-042: a nominal 500-600 km LEO payload (numbers modeled on the
    # Tracking Agent output example in the build plan).
    sat_042 = PredictionRequest(
        object_id="SAT-042",
        epoch_utc="2026-09-12T00:00:00Z",
        mean_elements=MeanElements(
            inclination_deg=51.6,
            raan_deg=100.0,
            eccentricity=0.0001234,
            arg_perigee_deg=90.0,
            mean_anomaly_deg=270.0,
            mean_motion_rev_day=15.5,
            bstar=1.2345e-4,
        ),
        **window,
    )

    # DEB-891: same orbital plane as SAT-042 (debris from the same family
    # of orbits is the realistic case), started ~120 km behind along-track
    # and given a very slightly faster mean motion so it closes that gap
    # and passes close by within the window, then continues to separate --
    # the build plan's synthetic fleet generator does exactly this trick
    # ("a few are guaranteed to conjunct").
    deb_891 = PredictionRequest(
        object_id="DEB-891",
        epoch_utc="2026-09-12T00:00:00Z",
        mean_elements=MeanElements(
            inclination_deg=51.6,
            raan_deg=100.0,
            eccentricity=0.0001800,
            arg_perigee_deg=90.0,
            mean_anomaly_deg=269.0,      # ~120 km behind SAT-042 along-track
            mean_motion_rev_day=15.5111,  # slightly faster -> closes the gap
            bstar=2.5e-4,
        ),
        **window,
    )

    print("Propagating SAT-042 ...")
    out_a = propagate(sat_042)
    print("Propagating DEB-891 ...")
    out_b = propagate(deb_891)

    for out in (out_a, out_b):
        if out.warnings:
            print(f"  [{out.object_id}] warnings: {out.warnings}")
        fpath = OUT_DIR / f"{out.object_id}.json"
        fpath.write_text(out.model_dump_json(indent=2, exclude={"warnings"}))
        print(f"  wrote {fpath} ({len(out.ephemeris)} samples) "
              "-- this is exactly the Collision Detection Agent's expected input shape")

    ephem_a = [p.model_dump() for p in out_a.ephemeris]
    ephem_b = [p.model_dump() for p in out_b.ephemeris]
    dist_km, t_min = min_separation(ephem_a, ephem_b)

    print()
    print(f"Minimum separation over the 24h window: {dist_km:.2f} km, at {t_min}")
    print("(SGP4's own accuracy floor is ~1-3 km over 24h for LEO -- treat this "
          "as a screening-grade number, exactly like real conjunction-assessment "
          "pipelines do, not a certified miss distance.)")


if __name__ == "__main__":
    main()
