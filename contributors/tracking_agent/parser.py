"""
parser.py — turns raw TLE lines into the Tracking Agent's output contract.

Uses `sgp4.api.Satrec.twoline2rv` purely as a validating parser: we read
off the already-unit-converted mean elements it exposes as attributes and
never call `.sgp4(...)` on the result (propagation is the Prediction
Agent's job, not this one -- see build plan sec. 1 "Math/standard
implemented": *no propagation happens in this agent*).
"""

import math
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec

from .tle_utils import validate_tle_pair, classify_object_type

RAD2DEG = 180.0 / math.pi
_RAD_PER_MIN_TO_REV_PER_DAY = 1440.0 / (2 * math.pi)


class TLEParseError(ValueError):
    """Raised when a TLE pair fails checksum/consistency validation or
    the underlying sgp4 library rejects the element set."""


def julian_to_utc_datetime(jd: float, jd_frac: float) -> datetime:
    """Convert a (jd, jd_fraction) pair, as produced by sgp4's
    jdsatepoch/jdsatepochF attributes, into a UTC datetime.

    Standard Julian Date -> Gregorian calendar conversion (Fliegel &
    Van Flandern algorithm), good for any date after 1582.
    """
    jd_total = jd + jd_frac
    jd_int = int(jd_total + 0.5)
    frac_day = (jd_total + 0.5) - jd_int

    a = jd_int + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153

    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10

    seconds_in_day = frac_day * 86400.0
    hour = int(seconds_in_day // 3600)
    minute = int((seconds_in_day % 3600) // 60)
    second = seconds_in_day - hour * 3600 - minute * 60
    microsecond = int(round((second - int(second)) * 1_000_000))
    second = int(second)

    # Handle rounding pushing seconds/microseconds to the next unit
    dt = datetime(year, month, day, hour, minute, second)
    dt += timedelta(microseconds=microsecond)
    return dt


def parse_tle_record(
    object_id: str,
    line1: str,
    line2: str,
    object_type: str | None = None,
    object_name: str | None = None,
    source: str = "unknown",
) -> dict:
    """Parse one TLE pair into the Tracking Agent output contract:

    {
      "object_id": str,
      "object_type": "PAYLOAD" | "ROCKET_BODY" | "DEBRIS",
      "epoch_utc": ISO-8601 str,
      "mean_elements": {
        "inclination_deg": float, "raan_deg": float, "eccentricity": float,
        "arg_perigee_deg": float, "mean_anomaly_deg": float,
        "mean_motion_rev_day": float, "bstar": float
      },
      "last_updated": ISO-8601 str,
      "source": str
    }

    `object_type`, if not supplied, is derived from `object_name` via the
    name-pattern fallback heuristic (see tle_utils.classify_object_type).
    Raises TLEParseError on checksum failure or malformed elements.
    """
    is_valid, reason = validate_tle_pair(line1, line2)
    if not is_valid:
        raise TLEParseError(f"invalid TLE for {object_id}: {reason}")

    try:
        sat = Satrec.twoline2rv(line1, line2)
    except Exception as exc:  # sgp4 raises plain RuntimeError/ValueError variants
        raise TLEParseError(f"sgp4 rejected TLE for {object_id}: {exc}") from exc

    if sat.error != 0:
        raise TLEParseError(f"sgp4 reported error code {sat.error} for {object_id}")

    epoch_dt = julian_to_utc_datetime(sat.jdsatepoch, sat.jdsatepochF)
    resolved_type = object_type or classify_object_type(object_name or "")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "object_id": object_id,
        "object_type": resolved_type,
        "epoch_utc": epoch_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mean_elements": {
            "inclination_deg": sat.inclo * RAD2DEG,
            "raan_deg": sat.nodeo * RAD2DEG,
            "eccentricity": sat.ecco,
            "arg_perigee_deg": sat.argpo * RAD2DEG,
            "mean_anomaly_deg": sat.mo * RAD2DEG,
            "mean_motion_rev_day": sat.no_kozai * _RAD_PER_MIN_TO_REV_PER_DAY,
            "bstar": sat.bstar,
        },
        "last_updated": now_iso,
        "source": source,
    }
