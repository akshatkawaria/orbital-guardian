"""
sources/celestrak.py — real-mode ingest from CelesTrak's GP API.

No auth required. We request FORMAT=JSON, which is CelesTrak's CCSDS-OMM
keyword set already split into named fields -- this gives us OBJECT_TYPE
for free (see implementation guide sec. 2.1/4) and skips fixed-width TLE
column parsing entirely for this path. Raw TLE parsing (parser.py) is
still exercised by the demo/synthetic path and by any pasted-TLE input.
"""

from datetime import datetime, timezone
from typing import Optional

import requests

from ..tle_utils import classify_object_type

BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = "OrbitalGuardian-TrackingAgent/1.0 (hackathon project; contact: team@example.org)"

# CelesTrak's own OBJECT_TYPE values, normalized to our schema.
_OBJECT_TYPE_MAP = {
    "PAYLOAD": "PAYLOAD",
    "ROCKET BODY": "ROCKET_BODY",
    "DEBRIS": "DEBRIS",
    "UNKNOWN": "PAYLOAD",  # safest default; refined by name heuristic below if still unknown
}


class CelesTrakError(RuntimeError):
    pass


def _get(params: dict) -> list[dict]:
    params = {**params, "FORMAT": "JSON"}
    resp = requests.get(BASE_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code != 200:
        raise CelesTrakError(f"CelesTrak returned HTTP {resp.status_code} for params={params}")
    data = resp.json()
    if not isinstance(data, list):
        raise CelesTrakError(f"unexpected CelesTrak response shape: {type(data)}")
    return data


def _omm_to_contract(omm: dict, source_tag: str) -> dict:
    """Convert one CelesTrak OMM-JSON record into our tracking contract shape."""
    object_id = str(omm.get("NORAD_CAT_ID") or omm.get("OBJECT_ID") or "UNKNOWN")
    raw_type = (omm.get("OBJECT_TYPE") or "UNKNOWN").upper()
    object_type = _OBJECT_TYPE_MAP.get(raw_type, "PAYLOAD")
    if raw_type == "UNKNOWN":
        object_type = classify_object_type(omm.get("OBJECT_NAME", ""))

    epoch_raw = omm["EPOCH"]  # CelesTrak gives ISO-ish "2026-09-11T18:23:41.472384"
    epoch_dt = datetime.fromisoformat(epoch_raw).replace(tzinfo=timezone.utc)

    return {
        "object_id": object_id,
        "object_type": object_type,
        "epoch_utc": epoch_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mean_elements": {
            "inclination_deg": float(omm["INCLINATION"]),
            "raan_deg": float(omm["RA_OF_ASC_NODE"]),
            "eccentricity": float(omm["ECCENTRICITY"]),
            "arg_perigee_deg": float(omm["ARG_OF_PERICENTER"]),
            "mean_anomaly_deg": float(omm["MEAN_ANOMALY"]),
            "mean_motion_rev_day": float(omm["MEAN_MOTION"]),
            "bstar": float(omm.get("BSTAR", 0.0)),
        },
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source_tag,
    }


def fetch_group(group: str) -> list[dict]:
    """Fetch a curated CelesTrak group (e.g. 'active', 'stations',
    'iridium-33-debris', 'last-30-days') and return contract-shaped records."""
    records = _get({"GROUP": group})
    return [_omm_to_contract(r, f"celestrak:group={group}") for r in records]


def fetch_catnr(catnr: int) -> Optional[dict]:
    """Fetch a single object by NORAD catalog number."""
    records = _get({"CATNR": catnr})
    if not records:
        return None
    return _omm_to_contract(records[0], f"celestrak:catnr={catnr}")


def fetch_intdes(intdes: str) -> list[dict]:
    """Fetch every object from one launch, by international designator
    (format 'yyyy-nnn'), e.g. all payloads/debris from a single Starlink launch."""
    records = _get({"INTDES": intdes})
    return [_omm_to_contract(r, f"celestrak:intdes={intdes}") for r in records]


def fetch_name(name_substring: str) -> list[dict]:
    """Fetch all objects whose name contains the given substring."""
    records = _get({"NAME": name_substring})
    return [_omm_to_contract(r, f"celestrak:name={name_substring}") for r in records]
