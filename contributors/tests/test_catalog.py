import os
import tempfile

import pytest

from tracking_agent.catalog import Catalog

SAMPLE = {
    "object_id": "SAT-042",
    "object_type": "PAYLOAD",
    "epoch_utc": "2026-09-12T00:00:00Z",
    "mean_elements": {
        "inclination_deg": 51.6, "raan_deg": 100.0, "eccentricity": 0.0001234,
        "arg_perigee_deg": 90.0, "mean_anomaly_deg": 270.0,
        "mean_motion_rev_day": 15.5, "bstar": 1.2345e-4,
    },
    "last_updated": "2026-09-12T00:00:00Z",
    "source": "test",
}


@pytest.fixture
def catalog():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    cat = Catalog(db_path=path)
    yield cat
    cat.close()
    os.remove(path)


def test_upsert_and_get(catalog):
    assert catalog.upsert(SAMPLE) is True
    fetched = catalog.get("SAT-042")
    assert fetched["mean_elements"]["inclination_deg"] == 51.6


def test_idempotent_upsert_same_record(catalog):
    catalog.upsert(SAMPLE)
    catalog.upsert(SAMPLE)  # identical epoch, re-applied
    assert catalog.count() == 1


def test_stale_record_rejected(catalog):
    catalog.upsert(SAMPLE)
    stale = dict(SAMPLE, epoch_utc="2026-09-01T00:00:00Z")  # older epoch
    written = catalog.upsert(stale)
    assert written is False
    # stored epoch should remain the newer one
    assert catalog.get("SAT-042")["epoch_utc"] == "2026-09-12T00:00:00Z"


def test_newer_record_overwrites(catalog):
    catalog.upsert(SAMPLE)
    newer = dict(SAMPLE, epoch_utc="2026-09-13T00:00:00Z")
    written = catalog.upsert(newer)
    assert written is True
    assert catalog.get("SAT-042")["epoch_utc"] == "2026-09-13T00:00:00Z"


def test_upsert_many_batch(catalog):
    a = dict(SAMPLE, object_id="A")
    b = dict(SAMPLE, object_id="B")
    result = catalog.upsert_many([a, b, a])  # 'a' twice on purpose
    # equal-epoch resubmission is a normal accepted re-poll, not a rejection,
    # so all three upserts land -- only a *strictly older* epoch is rejected.
    assert result["written"] == 3
    assert catalog.count() == 2


def test_three_refresh_cycles_stay_consistent(catalog):
    """Mirrors the build plan's demo-target check: repeated refreshes of
    the same batch must not duplicate or regress the catalog."""
    batch = [dict(SAMPLE, object_id=f"OBJ-{i}") for i in range(10)]
    for _ in range(3):
        catalog.upsert_many(batch)
    assert catalog.count() == 10


def test_stats_by_type(catalog):
    catalog.upsert(dict(SAMPLE, object_id="P1", object_type="PAYLOAD"))
    catalog.upsert(dict(SAMPLE, object_id="D1", object_type="DEBRIS"))
    stats = catalog.stats()
    assert stats["total_objects"] == 2
    assert stats["by_object_type"]["PAYLOAD"] == 1
    assert stats["by_object_type"]["DEBRIS"] == 1
