import numpy as np
import pytest

from lensder.core import lens as L
from lensder.core import overlay_geom as G
from lensder.core import schema


def _state(model, focal=24.0, **kw):
    p = schema.default_params()
    p.update(kw)
    return L.LensState(model=model, focal=focal, frame_w=36.0, frame_h=20.25, params=p)


@pytest.mark.parametrize("model,kw", [
    ("NONE", {}), ("BROWN", dict(k1=-0.1)), ("TDE_ANAMORPHIC", dict(cx02=0.06)),
    ("FISHEYE", dict(fish_proj=1, fish_fov=190.0)),
])
def test_grid_lines_cover_the_frame(model, kw):
    # 15mm equisolid: the 190° image circle (r = 22 mm) covers the 36 mm frame
    st = _state(model, focal=15.0 if model == "FISHEYE" else 24.0, **kw)
    lines = G.grid_polylines(st, lines=8)
    assert len(lines) >= 10
    pts = np.array([p for line in lines for p in line])
    # Lines reach (about) the frame edges
    assert pts[:, 0].max() > 0.45 * 36.0 and pts[:, 0].min() < -0.45 * 36.0


def test_ideal_lens_grid_is_straight():
    lines = G.grid_polylines(_state("NONE"), lines=4)
    for line in lines:
        a = np.array(line)
        assert np.ptp(a[:, 0]) < 1e-9 or np.ptp(a[:, 1]) < 1e-9


def test_barrel_bends_lines_inward_at_edges():
    lines = G.grid_polylines(_state("BROWN", k1=-0.15), lines=4)
    # The outermost vertical line: its middle is further out than its ends (barrel bulge)
    verticals = [np.array(line) for line in lines if np.ptp(np.array(line)[:, 1]) > np.ptp(np.array(line)[:, 0])]
    right = max(verticals, key=lambda a: a[:, 0].mean())
    mid = right[len(right) // 2, 0]
    assert mid > right[0, 0] and mid > right[-1, 0]


def test_lines_are_clipped_to_frame():
    st = _state("FISHEYE", focal=15.0, fish_proj=1, fish_fov=190.0)
    pts = np.array([p for line in G.grid_polylines(st) for p in line])
    assert np.abs(pts[:, 0]).max() <= 18.0 + 1e-6 and np.abs(pts[:, 1]).max() <= 10.125 + 1e-6


def test_circular_fisheye_shows_image_circle():
    st = _state("FISHEYE", focal=8.0, fish_proj=1, fish_fov=180.0)
    pts = np.array([p for line in G.grid_polylines(st) for p in line])
    r = np.hypot(pts[:, 0], pts[:, 1]).max()
    assert r == pytest.approx(2 * 8.0 * np.sin(np.radians(45)), rel=1e-3)
