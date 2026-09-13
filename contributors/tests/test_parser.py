import pytest

from tracking_agent.parser import parse_tle_record, TLEParseError

ISS_L1 = "1 25544U 98067A   19249.04864348  .00001909  00000-0  40858-4 0  9990"
ISS_L2 = "2 25544  51.6464 320.1755 0007999  10.9066  53.2893 15.50437522187805"


def test_parse_iss_matches_known_values():
    record = parse_tle_record(
        object_id="25544", line1=ISS_L1, line2=ISS_L2, object_type="PAYLOAD", source="test"
    )
    me = record["mean_elements"]
    assert record["object_id"] == "25544"
    assert record["object_type"] == "PAYLOAD"
    assert me["inclination_deg"] == pytest.approx(51.6464, abs=1e-3)
    assert me["raan_deg"] == pytest.approx(320.1755, abs=1e-3)
    assert me["eccentricity"] == pytest.approx(0.0007999, abs=1e-6)
    assert me["arg_perigee_deg"] == pytest.approx(10.9066, abs=1e-3)
    assert me["mean_anomaly_deg"] == pytest.approx(53.2893, abs=1e-3)
    assert me["mean_motion_rev_day"] == pytest.approx(15.50437522, abs=1e-5)
    # Epoch: 2019, day-of-year 249.04864348
    assert record["epoch_utc"].startswith("2019-09-06")


def test_parse_rejects_bad_checksum():
    corrupted = ISS_L1[:-1] + "9"  # wrong checksum digit
    with pytest.raises(TLEParseError):
        parse_tle_record(object_id="25544", line1=corrupted, line2=ISS_L2)


def test_parse_falls_back_to_name_classification():
    record = parse_tle_record(
        object_id="25544", line1=ISS_L1, line2=ISS_L2, object_name="SOME OBJECT DEB"
    )
    assert record["object_type"] == "DEBRIS"
