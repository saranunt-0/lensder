import math

import numpy as np
import pytest

from lensder.core import exposure as E
from lensder.core import lens as L
from lensder.core import optics as O
from lensder.core import schema
from lensder.core import stmap as S


def test_relative_stops():
    assert E.relative_stops(4.0, 1 / 50, 100, 2.8, 1 / 50, 100) == pytest.approx(-1.0, abs=0.03)
    assert E.relative_stops(2.8, 1 / 100, 100, 2.8, 1 / 50, 100) == pytest.approx(-1.0)
    assert E.relative_stops(2.8, 1 / 50, 200, 2.8, 1 / 50, 100) == pytest.approx(1.0)


def test_ev100_sunny16():
    # Sunny-16: f/16, 1/100 s, ISO 100 ≈ EV 14.6
    assert E.ev100(16, 1 / 100, 100) == pytest.approx(14.64, abs=0.01)


def test_shutter_conversions():
    assert E.shutter_angle_from_seconds(1 / 48, 24) == pytest.approx(180.0)
    assert E.motion_blur_fraction(1 / 48, 24) == pytest.approx(0.5)
    assert E.shutter_seconds_from_angle(90, 25) == pytest.approx(0.01)
    assert E.format_shutter(1 / 50) == "1/50"


def test_fit_gate_spherical_and_anamorphic():
    ff = O.fit_gate(36, 24, 1.0, 16 / 9)
    assert ff.fit == "HORIZONTAL" and ff.frame_w == 36 and ff.frame_h == pytest.approx(20.25)
    # 2x anamorphic on a 4:3 gate desqueezes to 48 x 18 (2.67:1); a 2.39:1
    # delivery is narrower, so it is limited by height and crops the sides.
    ana = O.fit_gate(24.0, 18.0, 2.0, 2.39)
    assert ana.fit == "VERTICAL" and ana.frame_h == 18.0
    assert ana.frame_w == pytest.approx(18.0 * 2.39)
    wider = O.fit_gate(24.0, 18.0, 2.0, 3.0)
    assert wider.fit == "HORIZONTAL" and wider.frame_w == pytest.approx(48.0)
    tall = O.fit_gate(24.0, 18.0, 2.0, 1.78)
    assert tall.fit == "VERTICAL" and tall.frame_h == 18.0 and tall.frame_w == pytest.approx(18 * 1.78)
    over = O.fit_gate(36, 24, 1.0, 16 / 9, overscan=1.1)
    assert over.sensor_width == pytest.approx(39.6) and over.frame_w == 36


def test_depth_of_field_reference_values():
    # Textbook thin-lens DOF (as in DOFMaster): 50mm f/2.8 @ 3 m, CoC 0.03 mm
    # H = f²/(N·c) + f = 29.81 m, near = 2.73 m, far = 3.33 m
    near, far, hyper = O.depth_of_field(50, 2.8, 3.0, 0.03)
    assert near == pytest.approx(2.73, abs=0.005)
    assert far == pytest.approx(3.33, abs=0.005)
    assert hyper == pytest.approx(29.81, abs=0.01)
    assert O.depth_of_field(24, 8, 10.0, 0.029)[1] == math.inf


def _state(model="NONE", **kw):
    p = schema.default_params()
    p.update(kw)
    return L.LensState(model=model, focal=35.0, frame_w=36.0, frame_h=20.25, params=p)


def test_identity_stmap():
    m = S.bake(_state(), 32, 18, 1.0)
    xs = (np.arange(32) + 0.5) / 32
    ys = (np.arange(18) + 0.5) / 18
    np.testing.assert_allclose(m[0, :, 0], xs, atol=1e-6)
    np.testing.assert_allclose(m[:, 0, 1], ys, atol=1e-6)
    assert (m[..., 2] == 1).all() and (m[..., 3] == 1).all()


def test_overscan_barrel_vs_pincushion():
    barrel = _state("BROWN", k1=-0.1)
    pin = _state("BROWN", k1=0.1)
    assert S.required_overscan(barrel) > 1.02
    # Pincushion needs no overscan (only the 1% safety margin remains).
    assert S.required_overscan(pin) <= 1.01


def test_stmap_full_coverage_with_required_overscan():
    st = _state("BROWN", k1=-0.12, k2=0.02)
    s = S.required_overscan(st)
    m = S.bake(st, 96, 54, s)
    assert m[..., 2].min() == 1.0
    m_tight = S.bake(st, 96, 54, 1.0)
    assert m_tight[..., 2].min() == 0.0  # corners would be black without overscan


def test_stmap_matches_ray_mapping():
    st = _state("TDE_ANAMORPHIC", cx02=0.05, cy02=0.02)
    s = S.required_overscan(st)
    m = S.bake(st, 40, 24, s, step=1)
    j, i = 5, 33
    px = ((i + 0.5) / 40 - 0.5) * st.frame_w
    py = ((j + 0.5) / 24 - 0.5) * st.frame_h
    dx, dy, dz, _ = L.film_to_ray(st, np.array([px]), np.array([py]))
    assert m[j, i, 0] == pytest.approx(0.5 + 35 * dx[0] / dz[0] / (st.frame_w * s), abs=1e-6)


@pytest.mark.parametrize("model,params", [
    ("BROWN", dict(k1=-0.12, k2=0.02, p1=0.002)),
    ("TDE_ANAMORPHIC", dict(cx02=0.06, cy02=0.03, cx04=0.01)),
    ("FISHEYE", dict(fish_proj=1, fish_fov=180.0)),
])
def test_lattice_bake_matches_exact(model, params):
    st = _state(model, **params)
    if model == "FISHEYE":
        st.focal = 8.0
    s = S.required_overscan(st)
    exact = S.bake(st, 160, 90, s, step=1)
    fast = S.bake(st, 160, 90, s, step=4)
    both = (exact[..., 2] == 1) & (fast[..., 2] == 1)
    # UV error in pixels of the pinhole render
    err_px = np.abs(exact[..., :2] - fast[..., :2])[both] * np.array([160, 90]) * s
    assert err_px.max() < 0.02
    assert (exact[..., 2] != fast[..., 2]).mean() < 0.002  # coverage edge agrees
