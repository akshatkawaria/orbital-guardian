from tracking_agent.tle_utils import (
    tle_line_checksum,
    validate_tle_line,
    validate_tle_pair,
    decode_alpha5,
    classify_object_type,
)

# Real, published ISS (ZARYA) TLE, catalog 25544
ISS_L1 = "1 25544U 98067A   19249.04864348  .00001909  00000-0  40858-4 0  9990"
ISS_L2 = "2 25544  51.6464 320.1755 0007999  10.9066  53.2893 15.50437522187805"


def test_iss_checksums_valid():
    assert validate_tle_line(ISS_L1)
    assert validate_tle_line(ISS_L2)


def test_pair_validation():
    ok, reason = validate_tle_pair(ISS_L1, ISS_L2)
    assert ok, reason


def test_checksum_rejects_corruption():
    corrupted = ISS_L1[:20] + "9" + ISS_L1[21:]  # flip one digit, leave checksum untouched
    assert not validate_tle_line(corrupted)


def test_catalog_number_mismatch_detected():
    # Swap catalog number but recompute the checksum so this test isolates
    # the mismatch check from the checksum check.
    body = "2 99999" + ISS_L2[7:-1]
    bad_l2 = body + str(tle_line_checksum(body))
    ok, reason = validate_tle_pair(ISS_L1, bad_l2)
    assert not ok
    assert "mismatch" in reason


def test_alpha5_numeric_passthrough():
    assert decode_alpha5("25544") == 25544


def test_alpha5_letter_decode():
    # E=10+4=14 -> 14*10000 + 8493 = 148493
    assert decode_alpha5("E8493") == 148493


def test_classify_debris():
    assert classify_object_type("COSMOS 2251 DEB") == "DEBRIS"


def test_classify_rocket_body():
    assert classify_object_type("CZ-6A R/B") == "ROCKET_BODY"


def test_classify_default_payload():
    assert classify_object_type("STARLINK-1234") == "PAYLOAD"
