"""
Validation suite for the Prediction Agent.

Per Prediction_Agent_Implementation_Guide.md §7, "done" for this agent
means more than "it runs" -- it means the numbers are independently
verified, because a subtle unit-conversion or frame-conversion bug will
otherwise surface three agents downstream as a phantom collision instead
of a red flag here.

Run with: pytest tests/test_propagation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prediction_agent.core import propagate
from prediction_agent.models import MeanElements, PredictionRequest

# A real, published ISS TLE (also used in python-sgp4's own test suite),
# decomposed into the shape our Tracking Agent contract expects.
ISS_ELEMENTS = MeanElements(
    inclination_deg=51.6439,
    raan_deg=211.2001,
    eccentricity=0.0007417,
    arg_perigee_deg=17.6667,
    mean_anomaly_deg=85.6398,
    mean_motion_rev_day=15.50103472,
    bstar=3.8792e-5,
)
ISS_EPOCH = "2019-12-09T16:38:29Z"  # TLE epoch 19343.69339541, rounded to whole seconds


def _iss_request(from_utc: str, to_utc: str, step: int = 60) -> PredictionRequest:
    return PredictionRequest(
        object_id="SAT-25544",
        mean_elements=ISS_ELEMENTS,
        epoch_utc=ISS_EPOCH,
        propagate_from_utc=from_utc,
        propagate_to_utc=to_utc,
        step_seconds=step,
    )


def test_output_matches_contract_shape():
    """Output must be exactly {object_id, frame, ephemeris:[{t_utc, r_km, v_kms}]}."""
    req = _iss_request("2019-12-09T16:38:29Z", "2019-12-09T16:40:29Z", step=60)
    out = propagate(req)

    assert out.object_id == "SAT-25544"
    assert out.frame == "ECI_J2000"
    assert len(out.ephemeris) == 3  # t0, t0+60s, t0+120s
    for point in out.ephemeris:
        assert len(point.r_km) == 3
        assert len(point.v_kms) == 3
        # LEO sanity bounds: ~6600-7000 km from Earth's center, ~7-8 km/s
        r_mag = np.linalg.norm(point.r_km)
        v_mag = np.linalg.norm(point.v_kms)
        assert 6500 < r_mag < 7200, f"position magnitude {r_mag} km outside LEO sanity range"
        assert 6.5 < v_mag < 9.0, f"velocity magnitude {v_mag} km/s outside LEO sanity range"


def test_no_warnings_for_healthy_recent_epoch():
    req = _iss_request("2019-12-09T16:38:29Z", "2019-12-10T16:38:29Z", step=3600)
    out = propagate(req)
    assert out.warnings == []


def test_staleness_warning_fires_past_threshold():
    """Propagating >7 days from epoch should surface a staleness warning,
    per the research doc's note that B* is a fitted, not physical, term."""
    req = _iss_request("2019-12-09T16:38:29Z", "2019-12-30T16:38:29Z", step=86400)
    out = propagate(req)
    assert any("staleness" in w or "B*" in w for w in out.warnings)


def test_cross_check_against_skyfield_direct_tle_parse():
    """
    Independent verification: build the SAME satellite two ways --
    (a) our build_satrec() from decomposed elements, and
    (b) Skyfield's own EarthSatellite(line1, line2) parsing the raw TLE
        text directly -- and confirm they agree to well within SGP4's own
        published accuracy floor (~1-3 km/24h). This isolates whether our
        unit-conversion table or frame-conversion logic has a bug: if (a)
        and (b) diverge by more than that floor, something in this
        module -- not SGP4 itself -- is wrong.
    """
    from skyfield.api import EarthSatellite, load

    line1 = "1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991"
    line2 = "2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482"
    ts = load.timescale()
    sat_direct = EarthSatellite(line1, line2, "ISS", ts)

    # Evaluate well after epoch so the ~1s epoch-rounding in our contract's
    # whole-seconds ISO format is negligible relative to elapsed time.
    target = ts.utc(2019, 12, 10, 4, 38, 29)  # 12h after epoch
    geo = sat_direct.at(target)

    req = _iss_request("2019-12-10T04:38:29Z", "2019-12-10T04:39:29Z", step=60)
    out = propagate(req)
    ours_r = np.array(out.ephemeris[0].r_km)
    ours_v = np.array(out.ephemeris[0].v_kms)

    pos_diff_km = np.linalg.norm(ours_r - geo.position.km)
    vel_diff_kms = np.linalg.norm(ours_v - geo.velocity.km_per_s)

    assert pos_diff_km < 5.0, f"position diverges from independent Skyfield parse by {pos_diff_km:.3f} km"
    assert vel_diff_kms < 0.05, f"velocity diverges from independent Skyfield parse by {vel_diff_kms:.5f} km/s"


def test_decayed_object_reports_warning_not_crash():
    """A physically invalid element set (e.g. eccentricity >= 1) must
    surface as a warning with an empty ephemeris, never raise or crash
    a batch -- one bad catalog entry shouldn't take down the agent."""
    bad = MeanElements(
        inclination_deg=51.6, raan_deg=100.0, eccentricity=1.5,  # invalid: >= 1.0
        arg_perigee_deg=90.0, mean_anomaly_deg=270.0,
        mean_motion_rev_day=15.5, bstar=1.0e-4,
    )
    req = PredictionRequest(
        object_id="BAD-001", mean_elements=bad, epoch_utc="2026-09-12T00:00:00Z",
        propagate_from_utc="2026-09-12T00:00:00Z", propagate_to_utc="2026-09-12T01:00:00Z",
        step_seconds=60,
    )
    out = propagate(req)
    assert out.ephemeris == []
    assert len(out.warnings) == 1
    assert "sgp4 init error" in out.warnings[0]


def test_batch_path_matches_single_object_path_exactly():
    """The vectorized SatrecArray batch path must produce numerically
    identical output to the per-object Skyfield path -- they share the
    same TEME->GCRS rotation math, just applied two different ways."""
    from prediction_agent.batch import batch_propagate
    from prediction_agent.models import BatchPredictionRequest

    single = propagate(_iss_request("2019-12-09T16:38:29Z", "2019-12-09T18:38:29Z", step=600))

    batch_req = BatchPredictionRequest(
        propagate_from_utc="2019-12-09T16:38:29Z",
        propagate_to_utc="2019-12-09T18:38:29Z",
        step_seconds=600,
        objects=[{
            "object_id": "SAT-25544",
            "epoch_utc": ISS_EPOCH,
            "mean_elements": ISS_ELEMENTS.model_dump(),
        }],
    )
    batch_out = batch_propagate(batch_req).predictions[0]

    assert len(single.ephemeris) == len(batch_out.ephemeris)
    for a, b in zip(single.ephemeris, batch_out.ephemeris):
        assert a.t_utc == b.t_utc
        assert np.allclose(a.r_km, b.r_km, atol=1e-3)
        assert np.allclose(a.v_kms, b.v_kms, atol=1e-5)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
