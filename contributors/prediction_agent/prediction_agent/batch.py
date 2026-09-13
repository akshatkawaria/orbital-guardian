"""
Batch/vectorized propagation path.

The per-object Skyfield loop in core.py is the right *default*: it's
simple and its TEME->ECI conversion is fully library-managed. But once the
Collision Detection Agent needs a full catalog (dozens-hundreds of objects
x thousands of timesteps) propagated for a screening pass, a Python-level
loop that calls .at() once per object becomes the bottleneck.

This module propagates every object against every timestep in one
vectorized sgp4.api.SatrecArray call, then rotates the whole TEME stack to
GCRS/J2000 using the *exact same rotation matrix* Skyfield's own
EarthSatellite._at() applies internally (skyfield.sgp4lib.TEME.rotation_at).
That means this fast path and the simple per-object path in core.py always
agree to numerical precision -- there is only one implementation of the
TEME->ECI transform in this codebase, just applied two different ways.

Output shape is identical to calling core.propagate() once per object;
this module exists purely for throughput, not a different contract.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
from sgp4.api import SatrecArray, jday
from skyfield.api import load
from skyfield.sgp4lib import TEME

from .core import build_satrec, SGP4_ERROR_MESSAGES, _STALENESS_WARNING_DAYS, _parse_utc
from .models import BatchPredictionRequest, BatchPredictionOutput, MeanElements, PredictionOutput

_TS = load.timescale()
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _teme_to_gcrs(r_teme: np.ndarray, v_teme: np.ndarray, rot: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    r_teme, v_teme: shape (n_steps, 3), km and km/s, in TEME.
    rot: shape (3, 3, n_steps) -- TEME.rotation_at(t) for the same n_steps.
    Returns (r_gcrs, v_gcrs), same shape, matching skyfield's own
    `R = _T(TEME.rotation_at(t)); r = mxv(R, r)` convention exactly.
    """
    # r_gcrs[k, i] = sum_j rot[j, i, k] * r_teme[k, j]   (i.e. rot[:,:,k].T @ r_teme[k])
    r_gcrs = np.einsum("jik,kj->ki", rot, r_teme)
    v_gcrs = np.einsum("jik,kj->ki", rot, v_teme)
    return r_gcrs, v_gcrs


def batch_propagate(request: BatchPredictionRequest) -> BatchPredictionOutput:
    t0 = _parse_utc(request.propagate_from_utc)
    t1 = _parse_utc(request.propagate_to_utc)
    step = timedelta(seconds=request.step_seconds)
    n_steps = int((t1 - t0) / step) + 1
    times = [t0 + i * step for i in range(n_steps)]

    # Julian date grid built the same way sgp4.api.jday builds it for the
    # single-object path -- keeps both propagation paths time-consistent.
    jd_list, fr_list = zip(*(
        jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
        for t in times
    ))
    jd_arr = np.array(jd_list)
    fr_arr = np.array(fr_list)

    # Skyfield Time object over the same grid, purely to get the
    # TEME->GCRS rotation matrices (theta_GMST1982 + precession/nutation).
    t_sky = _TS.utc(
        [t.year for t in times], [t.month for t in times], [t.day for t in times],
        [t.hour for t in times], [t.minute for t in times],
        [t.second + t.microsecond / 1e6 for t in times],
    )
    rot = TEME.rotation_at(t_sky)  # shape (3, 3, n_steps)

    satrecs = []
    object_ids = []
    warnings_by_object: dict[str, list[str]] = {}

    for obj in request.objects:
        oid = obj["object_id"]
        me = obj["mean_elements"]
        epoch_utc = obj.get("epoch_utc", request.propagate_from_utc)
        sat = build_satrec(oid, MeanElements(**me), epoch_utc)
        warnings_by_object[oid] = []

        if sat.error != 0:
            warnings_by_object[oid].append(
                f"sgp4 init error {sat.error}: {SGP4_ERROR_MESSAGES.get(sat.error, 'unknown')}"
            )

        epoch_dt = _parse_utc(epoch_utc)
        max_age_days = max(
            abs((t0 - epoch_dt).total_seconds()), abs((t1 - epoch_dt).total_seconds())
        ) / 86400.0
        if max_age_days > _STALENESS_WARNING_DAYS:
            warnings_by_object[oid].append(
                f"propagation window is {max_age_days:.1f} days from element epoch "
                f"({_STALENESS_WARNING_DAYS}-day staleness threshold exceeded)"
            )

        satrecs.append(sat)
        object_ids.append(oid)

    sat_array = SatrecArray(satrecs)
    err, r_teme, v_teme = sat_array.sgp4(jd_arr, fr_arr)
    # err.shape == (n_objects, n_steps); r_teme/v_teme.shape == (n_objects, n_steps, 3)

    predictions: list[PredictionOutput] = []
    for i, oid in enumerate(object_ids):
        r_gcrs, v_gcrs = _teme_to_gcrs(r_teme[i], v_teme[i], rot)

        ephemeris = []
        obj_warnings = list(warnings_by_object[oid])
        decayed_flagged = False
        for k, t in enumerate(times):
            if err[i, k] != 0:
                if not decayed_flagged:
                    obj_warnings.append(
                        f"sgp4 propagation error {err[i, k]} at step {k} "
                        f"({SGP4_ERROR_MESSAGES.get(int(err[i, k]), 'unknown')}); "
                        "remaining steps for this object may be invalid"
                    )
                    decayed_flagged = True
                continue  # skip invalid samples rather than emit garbage state
            ephemeris.append(
                {
                    "t_utc": t.strftime(_ISO_FMT),
                    "r_km": (round(float(r_gcrs[k, 0]), 3), round(float(r_gcrs[k, 1]), 3), round(float(r_gcrs[k, 2]), 3)),
                    "v_kms": (round(float(v_gcrs[k, 0]), 5), round(float(v_gcrs[k, 1]), 5), round(float(v_gcrs[k, 2]), 5)),
                }
            )

        predictions.append(PredictionOutput(object_id=oid, ephemeris=ephemeris, warnings=obj_warnings))

    return BatchPredictionOutput(
        screening_window={
            "start_utc": request.propagate_from_utc,
            "end_utc": request.propagate_to_utc,
        },
        predictions=predictions,
    )
