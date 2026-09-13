from tracking_agent.sources.synthetic import generate_synthetic_fleet


def test_generates_requested_count():
    fleet = generate_synthetic_fleet(n_objects=20, n_conjunction_pairs=2, seed=1)
    # 2 pairs = 4 objects guaranteed, plus filler up to 20 total
    assert len(fleet) == 20


def test_all_records_are_contract_shaped():
    fleet = generate_synthetic_fleet(n_objects=10, n_conjunction_pairs=1, seed=2)
    for record in fleet:
        assert set(record.keys()) >= {
            "object_id", "object_type", "epoch_utc", "mean_elements", "last_updated", "source"
        }
        me = record["mean_elements"]
        assert 0 <= me["inclination_deg"] <= 180
        assert 0 <= me["raan_deg"] < 360
        assert 0 <= me["eccentricity"] < 1
        assert 10 < me["mean_motion_rev_day"] < 17  # sane LEO range


def test_conjunction_pairs_are_close_in_phasing():
    """The first n_conjunction_pairs*2 records should be same-inclination,
    same-RAAN-family, closely phased pairs -- i.e. actually conjuncting
    candidates for the Prediction/Collision agents to find."""
    fleet = generate_synthetic_fleet(n_objects=6, n_conjunction_pairs=2, seed=3)
    pair_a, pair_b = fleet[0], fleet[1]
    assert abs(pair_a["mean_elements"]["inclination_deg"] - pair_b["mean_elements"]["inclination_deg"]) < 1.0
    raan_diff = abs(pair_a["mean_elements"]["raan_deg"] - pair_b["mean_elements"]["raan_deg"])
    assert min(raan_diff, 360 - raan_diff) < 1.0
    # Inclination/RAAN closeness alone is NOT sufficient for a near-circular
    # orbit (e ~ 0.0006 here) to actually produce a close approach -- the
    # in-plane position is fixed by the *argument of latitude*
    # u = arg_perigee + mean_anomaly (mod 360), not mean_anomaly alone.
    # This regression test would have caught the bug where arg_perigee was
    # independently randomized per object while only mean_anomaly was offset,
    # which passed the inclination/RAAN checks above while actually placing
    # the two objects ~234 degrees apart in their shared orbital plane.
    u_a = (pair_a["mean_elements"]["arg_perigee_deg"] + pair_a["mean_elements"]["mean_anomaly_deg"]) % 360
    u_b = (pair_b["mean_elements"]["arg_perigee_deg"] + pair_b["mean_elements"]["mean_anomaly_deg"]) % 360
    u_diff = abs(u_a - u_b)
    assert min(u_diff, 360 - u_diff) < 2.0, (
        f"argument of latitude differs by {min(u_diff, 360 - u_diff):.1f} deg -- "
        "pair is not actually on a close approach despite matching inclination/RAAN"
    )
