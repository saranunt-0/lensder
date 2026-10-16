import math

import numpy as np
import pytest

from lensder.core import lens as L
from lensder.core import schema


def make(model="NONE", focal=50.0, w=36.0, h=24.0, focus=0.0, **params):
    p = schema.default_params()
    p.update(params)
    return L.LensState(model=model, focal=focal, frame_w=w, frame_h=h, focus_distance=focus, params=p)


def grid(w, h, n=9):
    xs = np.linspace(-0.5 * w, 0.5 * w, n)
    ys = np.linspace(-0.5 * h, 0.5 * h, n)
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel()


def test_ideal_lens_is_pinhole():
    st = make()
    px, py = grid(36, 24)
    dx, dy, dz, ok = L.film_to_ray(st, px, py)
    assert ok.all()
    np.testing.assert_allclose(dx / dz * 50.0, px, atol=1e-12)
    np.testing.assert_allclose(dy / dz * 50.0, py, atol=1e-12)


ROUND_TRIP_CASES = [
    ("BROWN", dict(k1=-0.15, k2=0.03, p1=0.002, p2=-0.001)),
    ("BROWN", dict(k1=0.08, k2=-0.01, k3=0.004)),
    ("DIVISION", dict(div_k1=0.12, div_k2=-0.01)),
    ("NUKE", dict(nuke_k1=0.08, nuke_k2=0.01, nuke_p1=0.01, nuke_p2=-0.01)),
    ("TDE_RADIAL", dict(tde_c2=0.05, tde_u1=0.003, tde_v1=-0.002, tde_c4=0.01, tde_phi=20.0, tde_b=0.02)),
    ("TDE_ANAMORPHIC", dict(cx02=0.06, cy02=0.03, cx22=0.01, cy22=-0.005, cx04=0.015, cy04=0.005,
                            cx46=0.002, ana_rot=1.5, ana_sqx=1.01, ana_pa=2.0)),
    ("FISHEYE", dict(fish_proj=1, fish_k1=0.02, fish_fov=300.0)),
    ("FISHEYE", dict(fish_proj=2, fish_fov=300.0)),
]


@pytest.mark.parametrize("model,params", ROUND_TRIP_CASES)
def test_round_trip_film_ray_film(model, params):
    st = make(model, focal=24.0 if model != "FISHEYE" else 12.0, w=36.0, h=24.0,
              center_x=0.3, center_y=-0.2, **params)
    px, py = grid(36, 24, 11)
    dx, dy, dz, ok = L.film_to_ray(st, px, py)
    assert ok.all(), f"{model}: rays failed"
    bx, by, ok2 = L.ray_to_film(st, dx, dy, dz)
    assert ok2.all()
    np.testing.assert_allclose(bx, px, atol=1e-6)
    np.testing.assert_allclose(by, py, atol=1e-6)


def test_brown_barrel_sign_and_corner_readout():
    barrel = make("BROWN", focal=24.0, k1=-0.05)
    pin = make("BROWN", focal=24.0, k1=0.05)
    assert L.corner_distortion_percent(barrel) < 0 < L.corner_distortion_percent(pin)
    # Analytic check: the corner film point maps back to an ideal radius r_u with r_d = r_u (1 + k1 (r_u/f)^2).
    cd = L.corner_distortion_percent(barrel) / 100.0
    rd = math.hypot(18, 12)
    ru = rd / (1 + cd)
    assert ru * (1 - 0.05 * (ru / 24) ** 2) == pytest.approx(rd, rel=1e-9)


def _natron_anamorphic(xd, yd, c, rot, sqx, sqy, pa):
    """Independent re-implementation using Natron's *expanded* coefficients."""
    cphi, sphi = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    xd = xd / pa
    x = cphi * xd + sphi * yd
    y = -sphi * xd + cphi * yd
    x2, y2 = x * x, y * y
    x4, y4 = x2 * x2, y2 * y2
    x6, y6 = x4 * x2, y4 * y2

    def coeffs(a):
        g = lambda k: c.get(f"c{a}{k}", 0.0)  # noqa: E731
        return dict(
            x2=g("02") + g("22"), y2=g("02") - g("22"),
            x4=g("04") + g("24") + g("44"), x2y2=2 * g("04") - 6 * g("44"), y4=g("04") - g("24") + g("44"),
            x6=g("06") + g("26") + g("46") + g("66"),
            x4y2=3 * g("06") + g("26") - 5 * g("46") - 15 * g("66"),
            x2y4=3 * g("06") - g("26") - 5 * g("46") + 15 * g("66"),
            y6=g("06") - g("26") + g("46") - g("66"),
        )

    def poly(k):
        return (1 + x2 * k["x2"] + y2 * k["y2"] + x4 * k["x4"] + x2 * y2 * k["x2y2"] + y4 * k["y4"]
                + x6 * k["x6"] + x4 * y2 * k["x4y2"] + x2 * y4 * k["x2y4"] + y6 * k["y6"])

    xq = x * poly(coeffs("x")) * pa * sqx
    yq = y * poly(coeffs("y")) * sqy
    return cphi * xq - sphi * yq, sphi * xq + cphi * yq


def test_anamorphic_matches_natron_expansion():
    rng = np.random.default_rng(1)
    c = {f"c{a}{n}{m}": float(rng.normal(0, 0.02)) for m, n in schema.ANAMORPHIC_TERMS for a in "xy"}
    params = schema.default_params()
    params.update(c, ana_rot=2.0, ana_sqx=1.02, ana_sqy=0.99, ana_pa=1.8)
    x = rng.uniform(-1, 1, 200)
    y = rng.uniform(-0.7, 0.7, 200)
    ux, uy = L.tde_anamorphic_undistort(x, y, params)
    nx, ny = _natron_anamorphic(x, y, c, 2.0, 1.02, 0.99, 1.8)
    np.testing.assert_allclose(ux, nx, atol=1e-12)
    np.testing.assert_allclose(uy, ny, atol=1e-12)


def test_fisheye_projections_at_known_angles():
    f = 10.0
    for proj, r_of_theta in [(0, lambda t: f * t), (1, lambda t: 2 * f * math.sin(t / 2)),
                             (2, lambda t: 2 * f * math.tan(t / 2)), (3, lambda t: f * math.sin(t))]:
        st = make("FISHEYE", focal=f, fish_proj=proj, fish_fov=200.0)
        theta = math.radians(60)
        dx, dy, dz, ok = L.film_to_ray(st, np.array([r_of_theta(theta)]), np.array([0.0]))
        assert ok[0]
        assert math.degrees(math.acos(dz[0])) == pytest.approx(60.0, abs=1e-9)


def test_fisheye_image_circle_cuts_off():
    st = make("FISHEYE", focal=8.0, fish_proj=1, fish_fov=180.0)
    r90 = 2 * 8.0 * math.sin(math.radians(45))
    _, _, _, ok = L.film_to_ray(st, np.array([r90 * 0.99, r90 * 1.01]), np.array([0.0, 0.0]))
    assert ok.tolist() == [True, False]


def test_breathing_thin_lens():
    assert L.breathing_focal(50, 0.0, 1.0) == 50
    assert L.breathing_focal(50, 10.0, 0.0) == 50
    # Unit-focusing thin lens at 0.5 m: v = f*d/(d-f) = 50*500/450
    assert L.breathing_focal(50, 0.5, 1.0) == pytest.approx(50 * 500 / 450)


def test_breathing_narrows_field_of_view():
    far = make(focal=50.0, focus=100.0, breathing=1.0)
    near = make(focal=50.0, focus=0.5, breathing=1.0)
    _, _, dz_far, _ = L.film_to_ray(far, np.array([18.0]), np.array([0.0]))
    _, _, dz_near, _ = L.film_to_ray(near, np.array([18.0]), np.array([0.0]))
    assert dz_near[0] > dz_far[0]  # edge ray closer to the axis = narrower view


def test_vignette_polynomial_and_cos4():
    st = make(vig_k1=-0.5)
    v = L.vignette(st, np.array([0.0, 18.0]), np.array([0.0, 12.0]), np.array([1.0, 1.0]))
    np.testing.assert_allclose(v, [1.0, 0.5])
    st = make(cos4=1.0)
    v = L.vignette(st, np.array([0.0]), np.array([0.0]), np.array([math.cos(math.radians(40))]))
    assert v[0] == pytest.approx(math.cos(math.radians(40)) ** 4)


def test_newton_reports_failure_outside_model_domain():
    # Strong barrel: the forward map folds back, so far-out distorted points have no inverse.
    st = make("BROWN", focal=10.0, k1=-0.4)
    _, _, _, ok = L.film_to_ray(st, np.array([0.0, 30.0]), np.array([0.0, 0.0]))
    assert ok.tolist() == [True, False]
