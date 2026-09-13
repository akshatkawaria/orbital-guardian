"""
agent.py — the Tracking Agent's public interface.

This is the one class other agents (or the dashboard/orchestration API)
should import and call. It hides which data source was used behind a
single `refresh_*` call and always leaves the catalog in the JSON
contract shape defined in Orbital_Guardian_Build_Plan.md sec. 1.

Downstream integration contract (feeds the Prediction Agent):

    from tracking_agent.agent import TrackingAgent

    agent = TrackingAgent(db_path="tracking_catalog.db")
    agent.refresh_from_synthetic(n_objects=50, n_conjunction_pairs=3)
    catalog = agent.get_catalog()     # -> list[dict], contract-shaped
    one = agent.get_object("SIM-90001")

    # Prediction Agent then does, per object:
    #   propagate(one["mean_elements"], from=..., to=..., step=60)
"""

from typing import Optional

from .catalog import Catalog
from .parser import parse_tle_record, TLEParseError
from .sources import celestrak, synthetic
from .tle_utils import validate_tle_pair


class TrackingAgent:
    def __init__(self, db_path: str = "tracking_catalog.db"):
        self.catalog = Catalog(db_path=db_path)
        self._spacetrack_client = None  # lazily created, needs credentials

    # -- real mode: CelesTrak -------------------------------------------

    def refresh_from_celestrak_group(self, group: str) -> dict:
        """Pull a curated CelesTrak group and upsert every record.
        e.g. group='active', 'stations', 'iridium-33-debris', 'last-30-days'."""
        records = celestrak.fetch_group(group)
        return self.catalog.upsert_many(records)

    def refresh_from_celestrak_catnr(self, catnr: int) -> Optional[dict]:
        record = celestrak.fetch_catnr(catnr)
        if record is None:
            return None
        self.catalog.upsert(record)
        return record

    def refresh_from_celestrak_intdes(self, intdes: str) -> dict:
        records = celestrak.fetch_intdes(intdes)
        return self.catalog.upsert_many(records)

    # -- real mode: Space-Track (optional, requires credentials) --------

    def configure_spacetrack(self, identity: str, password: str):
        from .sources.spacetrack import SpaceTrackClient  # local import: avoid requiring
        self._spacetrack_client = SpaceTrackClient(identity, password)  # creds unless used

    def refresh_from_spacetrack_recent(self, epoch_days: int = 30, limit: int = 200) -> dict:
        if self._spacetrack_client is None:
            raise RuntimeError("call configure_spacetrack(identity, password) first")
        records = self._spacetrack_client.fetch_recent(epoch_days=epoch_days, limit=limit)
        return self.catalog.upsert_many(records)

    # -- demo mode: synthetic fleet ---------------------------------------

    def refresh_from_synthetic(
        self, n_objects: int = 50, n_conjunction_pairs: int = 3, seed: Optional[int] = 42
    ) -> dict:
        records = synthetic.generate_synthetic_fleet(
            n_objects=n_objects, n_conjunction_pairs=n_conjunction_pairs, seed=seed
        )
        return self.catalog.upsert_many(records)

    # -- manual ingest: a pasted / uploaded TLE --------------------------

    def ingest_raw_tle(
        self, object_id: str, line1: str, line2: str,
        object_type: Optional[str] = None, object_name: Optional[str] = None,
    ) -> dict:
        """Parse and store one hand-supplied TLE. Raises TLEParseError on
        a malformed pair -- callers (e.g. an operator paste-box in the
        dashboard) should catch this and surface it as a validation error,
        not a 500."""
        is_valid, reason = validate_tle_pair(line1, line2)
        if not is_valid:
            raise TLEParseError(f"rejected: {reason}")
        record = parse_tle_record(
            object_id=object_id, line1=line1, line2=line2,
            object_type=object_type, object_name=object_name, source="manual",
        )
        self.catalog.upsert(record)
        return record

    # -- reads, for downstream agents -----------------------------------

    def get_catalog(self, object_type: Optional[str] = None) -> list[dict]:
        """The full contract-shaped catalog. This is the exact feed the
        Prediction Agent's input schema expects, one dict per object."""
        return self.catalog.get_all(object_type=object_type)

    def get_object(self, object_id: str) -> Optional[dict]:
        return self.catalog.get(object_id)

    def stats(self) -> dict:
        return self.catalog.stats()

    def close(self):
        self.catalog.close()
