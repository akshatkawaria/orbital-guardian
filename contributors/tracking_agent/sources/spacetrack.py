"""
sources/spacetrack.py — real-mode ingest from Space-Track.org.

Requires a free account. Auth is a session-cookie login, not an API key
header, so this client holds a `requests.Session` across calls. Rate
limits are enforced per-account by Space-Track (roughly single digits of
requests per minute) -- callers should prefer batching one query per
refresh cycle over per-object polling, and should prefer CelesTrak
(sources/celestrak.py) unless Space-Track-only data is actually needed
(full SATCAT fields, CDMs, decay/launch-site metadata).
"""

from datetime import datetime, timezone
from typing import Optional

import requests

from ..tle_utils import classify_object_type

LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
QUERY_BASE = "https://www.space-track.org/basicspacedata/query"

_OBJECT_TYPE_MAP = {
    "PAYLOAD": "PAYLOAD",
    "ROCKET BODY": "ROCKET_BODY",
    "DEBRIS": "DEBRIS",
    "TBA": "PAYLOAD",
    "UNKNOWN": "PAYLOAD",
}


class SpaceTrackError(RuntimeError):
    pass


class SpaceTrackClient:
    """Thin session-authenticated client. Instantiate once per process
    and reuse -- re-authenticating per request will hit rate limits fast.
    """

    def __init__(self, identity: str, password: str):
        self._session = requests.Session()
        resp = self._session.post(
            LOGIN_URL, data={"identity": identity, "password": password}, timeout=30
        )
        if resp.status_code != 200 or "Login" in resp.text[:200]:
            raise SpaceTrackError(
                f"Space-Track login failed (HTTP {resp.status_code}); check credentials"
            )

    def _query(self, path: str) -> list[dict]:
        url = f"{QUERY_BASE}/{path}/format/json"
        resp = self._session.get(url, timeout=30)
        if resp.status_code != 200:
            raise SpaceTrackError(f"Space-Track query failed: HTTP {resp.status_code} for {path}")
        return resp.json()

    @staticmethod
    def _gp_to_contract(gp: dict, source_tag: str) -> dict:
        object_id = str(gp.get("NORAD_CAT_ID", "UNKNOWN"))
        raw_type = (gp.get("OBJECT_TYPE") or "UNKNOWN").upper()
        object_type = _OBJECT_TYPE_MAP.get(raw_type, "PAYLOAD")
        if raw_type == "UNKNOWN":
            object_type = classify_object_type(gp.get("OBJECT_NAME", ""))

        epoch_dt = datetime.fromisoformat(gp["EPOCH"]).replace(tzinfo=timezone.utc)

        return {
            "object_id": object_id,
            "object_type": object_type,
            "epoch_utc": epoch_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mean_elements": {
                "inclination_deg": float(gp["INCLINATION"]),
                "raan_deg": float(gp["RA_OF_ASC_NODE"]),
                "eccentricity": float(gp["ECCENTRICITY"]),
                "arg_perigee_deg": float(gp["ARG_OF_PERICENTER"]),
                "mean_anomaly_deg": float(gp["MEAN_ANOMALY"]),
                "mean_motion_rev_day": float(gp["MEAN_MOTION"]),
                "bstar": float(gp.get("BSTAR", 0.0)),
            },
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source_tag,
        }

    def fetch_catnr(self, catnr: int) -> Optional[dict]:
        records = self._query(f"class/gp/NORAD_CAT_ID/{catnr}")
        if not records:
            return None
        return self._gp_to_contract(records[0], f"spacetrack:catnr={catnr}")

    def fetch_recent(self, epoch_days: int = 30, limit: int = 200) -> list[dict]:
        """Fetch objects with an epoch within the last `epoch_days` days --
        a reasonable 'refresh the active catalog' query."""
        path = f"class/gp/EPOCH/%3Enow-{epoch_days}/orderby/EPOCH desc/limit/{limit}"
        records = self._query(path)
        return [self._gp_to_contract(r, "spacetrack:recent") for r in records]
