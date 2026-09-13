"""
Core propagation logic for the Prediction Agent.

Design decisions (see Prediction_Agent_Implementation_Guide.md for the full
reasoning):
  - Initialize sgp4's Satrec directly from the Tracking Agent's decomposed
    mean elements via sgp4init(), rather than round-tripping through
    synthetic TLE text.
  - Use WGS72 gravity constants (matches how public TLEs are fit; WGS84
    is *less* accurate here despite being the more modern geodetic model).
  - Wrap the Satrec in Skyfield's EarthSatellite so the TEME -> ECI/J2000
    (GCRS) rotation is the library-validated one, not a hand-rolled
    GMST-only shortcut.
  - Surface SGP4 error codes and TLE-staleness as `warnings` on the output
    rather than raising, so one bad object never kills a batch propagation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec, WGS72, jday
from skyfield.api import EarthSatellite, load

from .models import PredictionOutput, PredictionRequest, MeanElements

# SGP4 error codes -> human-readable reasons (per Vallado/Hoots reference impl)
SGP4_ERROR_MESSAGES = {
    0: "no error",
    1: "mean eccentricity out of range (>= 1.0 or < -0.001)",
    2: "mean motion less than 0.0",
    3: "perturbed eccentricity out of range (semi-latus rectum implies decayed orbit)",
    4: "semi-latus rectum < 0.0 (orbit has effectively decayed)",
    5: "epoch elements are sub-orbital",
    6: "satellite has decayed",
}

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_STALENESS_WARNING_DAYS = 7  # see research doc: B* is a fitted, not physical, drag term


def _parse_utc(s: str) -> datetime:
    """
    Parse an ISO-8601 UTC timestamp, tolerating an optional fractional-second
    component (e.g. "...T16:38:29.363424Z"). The output contract's examples
    only show whole seconds, but sub-second epoch precision genuinely
    matters here: a satellite moving at ~7-8 km/s accrues several km of
    position error per second of epoch rounding, so silently truncating
    fractional seconds (rather than accepting them if present) is a real,
    avoidable accuracy loss for a negligible amount of parsing code.
    """
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_satrec(object_id: str, mean_elements: MeanElements, epoch_utc: str) -> Satrec:
    """Initialize an sgp4 Satrec from decomposed mean elements (not TLE text)."""
    dt = _parse_utc(epoch_utc)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
    epoch_days = (jd + fr) - 2433281.5  # sgp4init wants days since 1949-12-31 00:00 UT

    # satnum is bookkeeping only; doesn't affect the physics. Fall back to a
    # stable hash if the object_id isn't numeric-suffixed.
    try:
        satnum = int("".join(ch for ch in object_id if ch.isdigit())[-5:] or "0")
    except ValueError:
        satnum = abs(hash(object_id)) % 100000

    sat = Satrec()
    sat.sgp4init(
        WGS72,
        "i",  # improved/AFSPC-compatible ops mode
        satnum,
        epoch_days,
        mean_elements.bstar,
        0.0,  # ndot: unused by SGP4 core, kept for TLE-record compatibility
        0.0,  # nddot: same
        mean_elements.eccentricity,
        math.radians(mean_elements.arg_perigee_deg),
        math.radians(mean_elements.inclination_deg),
        math.radians(mean_elements.mean_anomaly_deg),
        mean_elements.mean_motion_rev_day * 2 * math.pi / 1440.0,
        math.radians(mean_elements.raan_deg),
    )
    return sat


_TS = load.timescale(builtin=True)


def propagate(request: PredictionRequest) -> PredictionOutput:
    """Turn one Tracking Agent record + window into an ECI_J2000 ephemeris."""

    epoch_utc = request.epoch_utc or request.propagate_from_utc
    warnings: list[str] = []

    t0 = _parse_utc(request.propagate_from_utc)
    t1 = _parse_utc(request.propagate_to_utc)
    step = timedelta(seconds=request.step_seconds)

    # Staleness check: TLE drag term (B*) is a fitted proxy, not physical --
    # accuracy degrades faster than the nominal 1-3 km/24h figure once far
    # past epoch. Flag it rather than silently returning degraded numbers.
    epoch_dt = _parse_utc(epoch_utc)
    max_age_days = max(
        abs((t0 - epoch_dt).total_seconds()),
        abs((t1 - epoch_dt).total_seconds()),
    ) / 86400.0
    if max_age_days > _STALENESS_WARNING_DAYS:
        warnings.append(
            f"propagation window is {max_age_days:.1f} days from element epoch "
            f"({_STALENESS_WARNING_DAYS}-day staleness threshold exceeded); "
            "B* drag fit may no longer represent current atmospheric conditions"
        )

    sat_raw = build_satrec(request.object_id, request.mean_elements, epoch_utc)
    if sat_raw.error != 0:
        warnings.append(
            f"sgp4 init error {sat_raw.error}: "
            f"{SGP4_ERROR_MESSAGES.get(sat_raw.error, 'unknown error')}"
        )
        return PredictionOutput(object_id=request.object_id, ephemeris=[], warnings=warnings)

    sat = EarthSatellite.from_satrec(sat_raw, _TS)

    n_steps = int((t1 - t0) / step) + 1
    times = [t0 + i * step for i in range(n_steps)]
    t_sky = _TS.utc(
        [t.year for t in times],
        [t.month for t in times],
        [t.day for t in times],
        [t.hour for t in times],
        [t.minute for t in times],
        [t.second + t.microsecond / 1e6 for t in times],
    )

    geocentric = sat.at(t_sky)  # propagates TEME internally, returns GCRS (~= J2000)
    r = geocentric.position.km
    v = geocentric.velocity.km_per_s

    ephemeris = []
    for i, t in enumerate(times):
        ephemeris.append(
            {
                "t_utc": t.isoformat().replace("+00:00", "Z"),
                "r_km": tuple(float(r[k][i]) for k in range(3)),
                "v_kms": tuple(float(v[k][i]) for k in range(3)),
            }
        )

    return PredictionOutput(object_id=request.object_id, ephemeris=ephemeris, warnings=warnings)
