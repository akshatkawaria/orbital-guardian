"""
tle_utils.py — low-level TLE format helpers.

These are pure, dependency-free functions that operate on raw TLE text.
They exist separately from parser.py (which uses the `sgp4` library) so
that validation/classification logic can be unit-tested without needing
a full Satrec object, and so it can also be reused by other agents that
touch raw TLE text (e.g. an operator paste-box in the dashboard).
"""

import re

# Alpha-5 letters map: A-Z skipping I and O, each letter is worth 10x its
# position (starting at 10 for 'A') in the leading digit slot of a 5-char
# catalog number field. This is the scheme CelesTrak/Space-Track adopted
# once the classic 5-digit numeric catalog ran out of room.
_ALPHA5_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O intentionally skipped


def decode_alpha5(field: str) -> int:
    """Decode a 5-character catalog-number field that may use the Alpha-5
    convention (a leading letter in place of the leading digit) into a
    plain integer catalog number.

    Examples:
        "25544" -> 25544
        "E8493" -> 148493
    """
    field = field.strip()
    if not field:
        raise ValueError("empty catalog number field")
    first = field[0]
    if first.isdigit():
        return int(field)
    if first.upper() not in _ALPHA5_LETTERS:
        raise ValueError(f"unrecognized catalog number field: {field!r}")
    # Letter value: A=10, B=11, ... skipping I/O, up to Z=35 (approx range)
    letter_value = _ALPHA5_LETTERS.index(first.upper()) + 10
    rest = field[1:]
    return letter_value * 10000 + int(rest)


def tle_line_checksum(line: str) -> int:
    """Compute the mod-10 TLE checksum over columns 1-68 of a line.

    Rule: sum all digits; '-' counts as 1; every other character
    (letters, '.', '+', spaces) is ignored. Result is mod 10.
    """
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def validate_tle_line(line: str) -> bool:
    """Return True if `line` is at least 69 characters and its trailing
    checksum digit matches the computed checksum of columns 1-68."""
    line = line.rstrip("\n")
    if len(line) < 69:
        return False
    if not line[68].isdigit():
        return False
    return tle_line_checksum(line) == int(line[68])


def validate_tle_pair(line1: str, line2: str) -> tuple[bool, str]:
    """Validate a TLE pair's checksums and that both lines reference the
    same catalog number. Returns (is_valid, reason_if_invalid)."""
    if not validate_tle_line(line1):
        return False, "line1 checksum invalid"
    if not validate_tle_line(line2):
        return False, "line2 checksum invalid"
    catnr1 = line1[2:7].strip()
    catnr2 = line2[2:7].strip()
    if catnr1 != catnr2:
        return False, f"catalog number mismatch: line1={catnr1!r} line2={catnr2!r}"
    return True, ""


_DEB_RE = re.compile(r"\bDEB\b")
_RB_RE = re.compile(r"\bR/B\b")


def classify_object_type(object_name: str) -> str:
    """Name-pattern fallback classifier for PAYLOAD / ROCKET_BODY / DEBRIS.

    A raw TLE carries no object-type field at all -- that classification
    normally comes from a joined SATCAT record. This heuristic is the
    fallback path (see Tracking_Agent_Implementation_Guide.md sec. 4) used
    whenever a real-mode pull doesn't include SATCAT-sourced OBJECT_TYPE,
    and it's also usable standalone in tests/fixtures.
    """
    name = (object_name or "").upper()
    if _DEB_RE.search(name):
        return "DEBRIS"
    if _RB_RE.search(name):
        return "ROCKET_BODY"
    return "PAYLOAD"
