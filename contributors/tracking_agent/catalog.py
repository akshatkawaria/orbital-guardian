"""
catalog.py — the authoritative current-state store for the Tracking Agent.

Design rule (see Tracking_Agent_Implementation_Guide.md sec. 6.2):
upsert is keyed by object_id, and an incoming record is only written if
its epoch_utc is >= the epoch already stored for that object. This is
what keeps the catalog "internally consistent... across refresh cycles"
regardless of network reordering, duplicate pulls, or overlapping
CelesTrak group queries.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    object_id            TEXT PRIMARY KEY,
    object_type          TEXT NOT NULL CHECK(object_type IN ('PAYLOAD','ROCKET_BODY','DEBRIS')),
    epoch_utc            TEXT NOT NULL,
    inclination_deg      REAL NOT NULL,
    raan_deg             REAL NOT NULL,
    eccentricity         REAL NOT NULL,
    arg_perigee_deg      REAL NOT NULL,
    mean_anomaly_deg     REAL NOT NULL,
    mean_motion_rev_day  REAL NOT NULL,
    bstar                REAL NOT NULL,
    last_updated         TEXT NOT NULL,
    source               TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_objects_type ON objects(object_type);
"""

_UPSERT_SQL = """
INSERT INTO objects (object_id, object_type, epoch_utc, inclination_deg, raan_deg,
                      eccentricity, arg_perigee_deg, mean_anomaly_deg,
                      mean_motion_rev_day, bstar, last_updated, source)
VALUES (:object_id, :object_type, :epoch_utc, :inclination_deg, :raan_deg,
        :eccentricity, :arg_perigee_deg, :mean_anomaly_deg,
        :mean_motion_rev_day, :bstar, :last_updated, :source)
ON CONFLICT(object_id) DO UPDATE SET
    object_type=excluded.object_type, epoch_utc=excluded.epoch_utc,
    inclination_deg=excluded.inclination_deg, raan_deg=excluded.raan_deg,
    eccentricity=excluded.eccentricity, arg_perigee_deg=excluded.arg_perigee_deg,
    mean_anomaly_deg=excluded.mean_anomaly_deg,
    mean_motion_rev_day=excluded.mean_motion_rev_day, bstar=excluded.bstar,
    last_updated=excluded.last_updated, source=excluded.source
"""


def _flatten(record: dict) -> dict:
    """Flatten a contract-shaped record (nested mean_elements) into the
    flat column dict the SQL statements expect."""
    flat = {k: v for k, v in record.items() if k != "mean_elements"}
    flat.update(record["mean_elements"])
    flat.setdefault("source", "unknown")
    return flat


def _unflatten(row: sqlite3.Row) -> dict:
    """Reconstruct the nested contract shape from a DB row."""
    return {
        "object_id": row["object_id"],
        "object_type": row["object_type"],
        "epoch_utc": row["epoch_utc"],
        "mean_elements": {
            "inclination_deg": row["inclination_deg"],
            "raan_deg": row["raan_deg"],
            "eccentricity": row["eccentricity"],
            "arg_perigee_deg": row["arg_perigee_deg"],
            "mean_anomaly_deg": row["mean_anomaly_deg"],
            "mean_motion_rev_day": row["mean_motion_rev_day"],
            "bstar": row["bstar"],
        },
        "last_updated": row["last_updated"],
        "source": row["source"],
    }


class Catalog:
    """The Tracking Agent's persistent object catalog.

    Usable as a plain object in-process by other agents in the same
    codebase (`catalog.get_all()`), or wrapped by tracking_agent/api.py
    for cross-process / cross-language consumption over HTTP.
    """

    def __init__(self, db_path: str = "tracking_catalog.db"):
        self.db_path = db_path
        # check_same_thread=False: the FastAPI wrapper (api.py) runs each
        # request in a worker thread, and this Catalog instance is shared
        # across all of them (single-writer, hackathon-scale usage -- for
        # heavier concurrent write load, swap in a per-request connection
        # or a real connection pool instead of relaxing this flag).
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # -- writes ------------------------------------------------------

    def upsert(self, record: dict) -> bool:
        """Insert or update one contract-shaped record.

        Returns True if the record was written, False if it was rejected
        because a newer (or equal) epoch is already stored for that
        object_id -- rejection is a normal, expected outcome, not an
        error, so callers should check the return value rather than
        assume every upsert lands.
        """
        cur = self._conn.execute(
            "SELECT epoch_utc FROM objects WHERE object_id = ?", (record["object_id"],)
        )
        row = cur.fetchone()
        if row is not None and row["epoch_utc"] > record["epoch_utc"]:
            return False
        self._conn.execute(_UPSERT_SQL, _flatten(record))
        self._conn.commit()
        return True

    def upsert_many(self, records: Iterable[dict]) -> dict:
        """Batch upsert, wrapped as one transaction so a partially-failed
        refresh never leaves the catalog half-updated. Returns counts."""
        written, rejected = 0, 0
        try:
            for record in records:
                if self.upsert(record):
                    written += 1
                else:
                    rejected += 1
        except Exception:
            self._conn.rollback()
            raise
        return {"written": written, "rejected_stale": rejected}

    def delete(self, object_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM objects WHERE object_id = ?", (object_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def clear(self):
        self._conn.execute("DELETE FROM objects")
        self._conn.commit()

    # -- reads ---------------------------------------------------------

    def get(self, object_id: str) -> Optional[dict]:
        cur = self._conn.execute("SELECT * FROM objects WHERE object_id = ?", (object_id,))
        row = cur.fetchone()
        return _unflatten(row) if row else None

    def get_all(self, object_type: Optional[str] = None) -> list[dict]:
        """Return every catalog record in contract JSON shape -- this is
        exactly what the Prediction Agent (or any downstream agent)
        should call to get the current-state feed described in the
        build plan's data-flow contract."""
        if object_type:
            cur = self._conn.execute(
                "SELECT * FROM objects WHERE object_type = ? ORDER BY object_id", (object_type,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM objects ORDER BY object_id")
        return [_unflatten(row) for row in cur.fetchall()]

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM objects")
        return cur.fetchone()["n"]

    def age_hours(self, object_id: str) -> Optional[float]:
        """Hours since the stored element set's epoch (not since it was
        last polled) -- the staleness signal described in the
        implementation guide sec. 6.3."""
        record = self.get(object_id)
        if record is None:
            return None
        epoch = datetime.strptime(record["epoch_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return (datetime.now(timezone.utc) - epoch).total_seconds() / 3600.0

    def stats(self) -> dict:
        """Summary used by the dashboard's catalog table header and by
        the /catalog/stats API endpoint."""
        rows = self.get_all()
        by_type: dict[str, int] = {}
        stale_over_7d = 0
        for r in rows:
            by_type[r["object_type"]] = by_type.get(r["object_type"], 0) + 1
            age = self.age_hours(r["object_id"])
            if age is not None and age > 24 * 7:
                stale_over_7d += 1
        return {
            "total_objects": len(rows),
            "by_object_type": by_type,
            "stale_over_7_days": stale_over_7d,
        }
