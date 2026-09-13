import numpy as np
import pytest

from app.frame import build_local_frame, direction_unit_vector


def test_frame_is_orthonormal_circular_orbit():
    # Circular LEO-like orbit in the xy-plane.
    r = np.array([7000.0, 0.0, 0.0])
    v = np.array([0.0, 7.546, 0.0])

    frame = build_local_frame(r, v)

    for vec in (frame.radial, frame.transverse, frame.normal):
        assert np.isclose(np.linalg.norm(vec), 1.0)

    assert np.isclose(np.dot(frame.radial, frame.transverse), 0.0, atol=1e-9)
    assert np.isclose(np.dot(frame.radial, frame.normal), 0.0, atol=1e-9)
    assert np.isclose(np.dot(frame.transverse, frame.normal), 0.0, atol=1e-9)


def test_transverse_matches_velocity_direction_for_circular_orbit():
    r = np.array([7000.0, 0.0, 0.0])
    v = np.array([0.0, 7.546, 0.0])
    frame = build_local_frame(r, v)
    v_hat = v / np.linalg.norm(v)
    assert np.allclose(frame.transverse, v_hat, atol=1e-9)


def test_radial_points_away_from_origin():
    r = np.array([0.0, 7000.0, 0.0])
    v = np.array([-7.546, 0.0, 0.0])
    frame = build_local_frame(r, v)
    assert np.allclose(frame.radial, r / np.linalg.norm(r))


def test_direction_unit_vectors_are_opposite_pairs():
    r = np.array([7000.0, 0.0, 0.0])
    v = np.array([0.0, 7.546, 0.0])
    frame = build_local_frame(r, v)

    assert np.allclose(
        direction_unit_vector(frame, "prograde"),
        -direction_unit_vector(frame, "retrograde"),
    )
    assert np.allclose(
        direction_unit_vector(frame, "radial_out"),
        -direction_unit_vector(frame, "radial_in"),
    )
    assert np.allclose(
        direction_unit_vector(frame, "normal"),
        -direction_unit_vector(frame, "anti_normal"),
    )


def test_degenerate_state_raises():
    # Purely radial velocity -> zero angular momentum -> no orbital plane.
    r = np.array([7000.0, 0.0, 0.0])
    v = np.array([1.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        build_local_frame(r, v)


def test_unsupported_direction_raises():
    r = np.array([7000.0, 0.0, 0.0])
    v = np.array([0.0, 7.546, 0.0])
    frame = build_local_frame(r, v)
    with pytest.raises(ValueError):
        direction_unit_vector(frame, "sideways")
