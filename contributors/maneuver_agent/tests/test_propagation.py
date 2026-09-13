import math

import numpy as np

from app.propagation import MU_EARTH_KM3_S2, propagate_two_body


def test_zero_dt_returns_input_unchanged():
    r = np.array([7000.0, 0.0, 0.0])
    v = np.array([0.0, 7.546, 0.0])
    r2, v2 = propagate_two_body(r, v, 0.0)
    assert np.allclose(r, r2)
    assert np.allclose(v, v2)


def test_circular_orbit_returns_to_start_after_one_period():
    # For a circular orbit, period T = 2*pi*sqrt(a^3/mu).
    a = 7000.0
    v_circ = math.sqrt(MU_EARTH_KM3_S2 / a)
    r0 = np.array([a, 0.0, 0.0])
    v0 = np.array([0.0, v_circ, 0.0])

    period_s = 2 * math.pi * math.sqrt(a**3 / MU_EARTH_KM3_S2)

    r1, v1 = propagate_two_body(r0, v0, period_s)

    assert np.allclose(r1, r0, atol=1e-4)  # within ~100 mm after a full period
    assert np.allclose(v1, v0, atol=1e-7)


def test_circular_orbit_quarter_period_is_perpendicular():
    a = 7000.0
    v_circ = math.sqrt(MU_EARTH_KM3_S2 / a)
    r0 = np.array([a, 0.0, 0.0])
    v0 = np.array([0.0, v_circ, 0.0])
    period_s = 2 * math.pi * math.sqrt(a**3 / MU_EARTH_KM3_S2)

    r1, v1 = propagate_two_body(r0, v0, period_s / 4)

    # After a quarter period, position should have rotated ~90 degrees
    # in-plane: from (+a, 0, 0) to roughly (0, +a, 0).
    assert np.isclose(np.linalg.norm(r1), a, atol=1e-3)
    assert np.isclose(r1[0], 0.0, atol=1.0)
    assert np.isclose(r1[1], a, atol=1.0)
    assert np.isclose(r1[2], 0.0, atol=1e-6)


def test_energy_conservation():
    r0 = np.array([7000.0, 500.0, 100.0])
    v0 = np.array([0.5, 7.4, 0.2])
    mu = MU_EARTH_KM3_S2

    def specific_energy(r, v):
        return 0.5 * np.dot(v, v) - mu / np.linalg.norm(r)

    e0 = specific_energy(r0, v0)
    r1, v1 = propagate_two_body(r0, v0, 1800.0)
    e1 = specific_energy(r1, v1)

    assert math.isclose(e0, e1, rel_tol=1e-9)


def test_angular_momentum_conservation():
    r0 = np.array([7000.0, 500.0, 100.0])
    v0 = np.array([0.5, 7.4, 0.2])

    h0 = np.cross(r0, v0)
    r1, v1 = propagate_two_body(r0, v0, 1800.0)
    h1 = np.cross(r1, v1)

    assert np.allclose(h0, h1, rtol=1e-8)


def test_deterministic_repeat_calls():
    r0 = np.array([7000.0, 500.0, 100.0])
    v0 = np.array([0.5, 7.4, 0.2])
    r1a, v1a = propagate_two_body(r0, v0, 900.0)
    r1b, v1b = propagate_two_body(r0, v0, 900.0)
    assert np.array_equal(r1a, r1b)
    assert np.array_equal(v1a, v1b)
