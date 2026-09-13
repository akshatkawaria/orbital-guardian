"""
sources/synthetic.py — demo-mode fleet generator.

Produces real, checksum-valid TLE line pairs (not just parsed dicts) so
the demo exercises the exact same parser.py code path that real-mode
CelesTrak/Space-Track TLE data would (when the CATNR you paste in isn't
JSON-native, or when validating a pasted TLE from the dashboard). Per
the build plan: 20-100 objects, mostly random LEO shells, with a few
pairs deliberately phased to conjunct.
"""

import random
from datetime import datetime, timedelta, timezone

from ..parser import parse_tle_record

_MU_EARTH_KM3_S2 = 398600.4418
_EARTH_RADIUS_KM = 6378.137


def _mean_motion_from_altitude_km(altitude_km: float) -> float:
    """Circular-orbit mean motion (rev/day) for a given altitude."""
    r = _EARTH_RADIUS_KM + altitude_km
    period_s = 2 * 3.14159265358979 * (r ** 1.5) / (_MU_EARTH_KM3_S2 ** 0.5)
    return 86400.0 / period_s


def _checksum(line68: str) -> int:
    total = 0
    for ch in line68:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def _epoch_field(epoch_dt: datetime) -> str:
    """Format a datetime into the TLE 'yyddd.dddddddd' epoch field."""
    year2 = epoch_dt.year % 100
    day_of_year = (
        epoch_dt - datetime(epoch_dt.year, 1, 1, tzinfo=epoch_dt.tzinfo)
    ).total_seconds() / 86400.0 + 1.0
    return f"{year2:02d}{day_of_year:012.8f}"


def _build_tle_lines(
    catnr: int,
    epoch_dt: datetime,
    inclination_deg: float,
    raan_deg: float,
    eccentricity: float,
    arg_perigee_deg: float,
    mean_anomaly_deg: float,
    mean_motion_rev_day: float,
    bstar: float = 1.0e-4,
) -> tuple[str, str]:
    """Assemble a checksum-valid, column-correct TLE pair from element
    values. This is the inverse of parser.py -- useful only for demo
    data generation, never for real ingest."""
    ecc_field = f"{int(round(eccentricity * 1e7)):07d}"
    bstar_mantissa = bstar / (10 ** (-4)) if bstar != 0 else 0.0
    # Encode bstar in TLE's "leading-decimal + exponent" convention, e.g. 1.2345e-4 -> " 12345-4"
    if bstar == 0:
        bstar_field = " 00000-0"
    else:
        exp = 0
        mantissa = bstar
        while abs(mantissa) >= 1:
            mantissa /= 10
            exp += 1
        while abs(mantissa) < 0.1:
            mantissa *= 10
            exp -= 1
        sign = "-" if bstar < 0 else " "
        bstar_field = f"{sign}{int(round(abs(mantissa) * 1e5)):05d}{exp:+d}".replace("+", "")
        # normalize to fixed width " 12345-4" / "-12345-4"
        bstar_field = f"{sign}{int(round(abs(mantissa) * 1e5)):05d}{exp}"

    line1_body = (
        f"1 {catnr:05d}U 26001A   {_epoch_field(epoch_dt)}  .00000000  00000-0 "
        f"{bstar_field:>8s} 0  999"
    )
    line1 = line1_body + str(_checksum(line1_body))

    line2_body = (
        f"2 {catnr:05d} {inclination_deg:8.4f} {raan_deg:8.4f} {ecc_field} "
        f"{arg_perigee_deg:8.4f} {mean_anomaly_deg:8.4f} {mean_motion_rev_day:11.8f}00001"
    )
    line2 = line2_body + str(_checksum(line2_body))

    return line1, line2


def generate_synthetic_fleet(
    n_objects: int = 50,
    n_conjunction_pairs: int = 3,
    epoch: datetime | None = None,
    seed: int | None = 42,
) -> list[dict]:
    """Generate a demo fleet of contract-shaped tracking records.

    Strategy (implementation guide sec. 2.3):
      - most objects: LEO shells at 500-600 km, inclinations drawn from a
        few realistic sun-sync/ISS-like families, RAAN/mean-anomaly
        randomized -- spreads objects around the shell plausibly.
      - a handful of pairs: same inclination + RAAN family (coplanar),
        mean anomaly offset by a small closing amount, so the Prediction
        and Collision Detection agents have guaranteed, findable threats
        to demo against.
    """
    rng = random.Random(seed)
    epoch = epoch or datetime.now(timezone.utc)
    records: list[dict] = []
    catnr_counter = 90000  # synthetic range, clear of any real catalog numbers

    inclination_families_deg = [51.6, 97.4, 53.0, 42.0]  # ISS-like, sun-sync-like, Starlink-like

    def add_object(inclination, raan, eccentricity, arg_perigee, mean_anomaly, altitude_km, object_type):
        nonlocal catnr_counter
        catnr_counter += 1
        object_id = f"SIM-{catnr_counter}"
        mm = _mean_motion_from_altitude_km(altitude_km)
        line1, line2 = _build_tle_lines(
            catnr=catnr_counter,
            epoch_dt=epoch,
            inclination_deg=inclination,
            raan_deg=raan % 360.0,
            eccentricity=eccentricity,
            arg_perigee_deg=arg_perigee % 360.0,
            mean_anomaly_deg=mean_anomaly % 360.0,
            mean_motion_rev_day=mm,
            bstar=rng.uniform(0.5e-4, 3.0e-4),
        )
        record = parse_tle_record(
            object_id=object_id,
            line1=line1,
            line2=line2,
            object_type=object_type,
            source="synthetic",
        )
        records.append(record)
        return record

    # 1. Guaranteed-conjunction pairs first, so they're never crowded out
    #    if the caller asks for a small n_objects.
    for i in range(n_conjunction_pairs):
        inclination = rng.choice(inclination_families_deg)
        raan = rng.uniform(0, 360)
        altitude = rng.uniform(500, 600)
        # IMPORTANT: for a near-circular orbit (e ~ 0.0006, as used below),
        # argument of perigee is nearly undefined physically -- what actually
        # fixes in-plane position is the *argument of latitude*
        # u = arg_perigee + mean_anomaly (mod 360). Two objects only end up
        # near each other in the plane if inclination, RAAN, AND u all match
        # closely -- matching inclination/RAAN while independently
        # randomizing arg_perigee per object (even with mean_anomaly held
        # close) puts them at essentially random points around the same
        # ring, not on a close approach. So arg_perigee is drawn ONCE per
        # pair and shared, and only mean_anomaly gets the small offset that
        # is meant to represent the closing geometry.
        arg_perigee = rng.uniform(0, 360)
        base_anomaly = rng.uniform(0, 360)
        object_type_a = "PAYLOAD"
        object_type_b = "DEBRIS" if i % 2 == 0 else "PAYLOAD"
        # Small phasing offset (a few tenths of a degree in mean anomaly)
        # at matching inclination/RAAN/arg_perigee/altitude puts these on a
        # close, slowly-converging approach -- exactly what a screening pass
        # downstream should flag.
        add_object(inclination, raan, 0.0006, arg_perigee, base_anomaly, altitude, object_type_a)
        add_object(
            inclination,
            raan + rng.uniform(-0.05, 0.05),
            0.0006,
            arg_perigee,
            base_anomaly + rng.uniform(0.05, 0.3),
            altitude + rng.uniform(-0.5, 0.5),
            object_type_b,
        )

    # 2. Fill the rest of the fleet with independently randomized objects.
    remaining = max(0, n_objects - len(records))
    for _ in range(remaining):
        inclination = rng.choice(inclination_families_deg) + rng.uniform(-0.3, 0.3)
        raan = rng.uniform(0, 360)
        altitude = rng.uniform(450, 650)
        object_type = rng.choices(
            ["PAYLOAD", "DEBRIS", "ROCKET_BODY"], weights=[0.55, 0.35, 0.10]
        )[0]
        add_object(
            inclination, raan, rng.uniform(0.0002, 0.002), rng.uniform(0, 360),
            rng.uniform(0, 360), altitude, object_type,
        )

    return records
