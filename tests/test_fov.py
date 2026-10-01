"""Golden values for the rig field-of-view calculation."""

import pytest

from app.server import fov_arcmin


def test_full_frame_1000mm():
    # 36 mm sensor width behind a 1000 mm lens -> ~123.6 arcmin
    assert fov_arcmin(1000, 36) == pytest.approx(123.65, abs=0.15)


def test_full_frame_height_1000mm():
    assert fov_arcmin(1000, 24) == pytest.approx(82.47, abs=0.15)


def test_doubling_focal_halves_fov():
    wide = fov_arcmin(500, 36)
    tight = fov_arcmin(1000, 36)
    assert wide == pytest.approx(tight * 2 + 0.01, rel=0.02)  # tiny nonlinearity


def test_typical_reflector_setup():
    # 750 mm focal length with an APS-C sensor (22.3 x 14.9 mm)
    # FOV_w = 2*atan(11.15/750) = 1.703 deg = 102.2'
    w = fov_arcmin(750, 22.3)
    h = fov_arcmin(750, 14.9)
    assert w == pytest.approx(102.2, abs=0.4)
    assert h == pytest.approx(68.3, abs=0.4)


def test_monotonic_in_sensor_size():
    assert fov_arcmin(800, 36) > fov_arcmin(800, 24) > fov_arcmin(800, 12)
