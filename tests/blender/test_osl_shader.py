"""The generated OSL shader must reproduce the NumPy reference model in Cycles."""

import math

import numpy as np
import pytest

from tests.blender import bhelpers as H

bpy = H.bpy

from lensder.core import lens as L  # noqa: E402
from lensder.core import osl  # noqa: E402
from lensder.core import schema  # noqa: E402

RES = (64, 36)


def _setup_custom_camera(cam_ob, model, params, focal):
    cd = cam_ob.data
    txt = bpy.data.texts.get("lensder_test.osl") or bpy.data.texts.new("lensder_test.osl")
    txt.from_string(osl.source())
    cd.type = "CUSTOM"
    cd.custom_mode = "INTERNAL"
    cd.custom_shader = txt
    cc = cd.cycles_custom
    assert "model" in cc.keys(), f"shader did not compile; params: {list(cc.keys())}"
    cc["model"] = schema.MODEL_BY_ID[model].index
    cc["focal_length"] = focal
    for k, v in params.items():
        cc[k] = int(v) if schema.PARAM_BY_NAME[k].is_int else float(v)


def _expected_dirs(model, params, focal, frame_w, frame_h, focus=0.0):
    p = schema.default_params()
    p.update(params)
    st = L.LensState(model=model, focal=focal, frame_w=frame_w, frame_h=frame_h, focus_distance=focus, params=p)
    w, h = RES
    xs = ((np.arange(w) + 0.5) / w - 0.5) * frame_w
    ys = ((np.arange(h) + 0.5) / h - 0.5) * frame_h
    gx, gy = np.meshgrid(xs, ys)
    dx, dy, dz, ok = L.film_to_ray(st, gx, gy)
    return np.stack([dx, dy, dz], -1), ok, st, gx, gy


CASES = [
    ("NONE", {}),
    ("BROWN", dict(k1=-0.12, k2=0.02, p1=0.003, p2=-0.002, center_x=0.4)),
    ("DIVISION", dict(div_k1=0.1, div_k2=0.01)),
    ("NUKE", dict(nuke_k1=0.06, nuke_k2=0.01, nuke_p1=0.01, nuke_p2=-0.005)),
    ("TDE_RADIAL", dict(tde_c2=0.05, tde_u1=0.004, tde_v1=-0.003, tde_c4=0.01, tde_phi=25.0, tde_b=0.03)),
    ("TDE_ANAMORPHIC", dict(cx02=0.06, cy02=0.03, cx22=0.01, cy22=-0.006, cx04=0.012, cy04=0.004,
                            cx24=0.003, cy44=-0.002, cx26=0.002, cy66=0.001, ana_rot=2.0, ana_sqx=1.01,
                            ana_sqy=0.995, ana_pa=1.5)),
    ("FISHEYE", dict(fish_proj=1, fish_fov=200.0, fish_k1=0.01)),
    ("FISHEYE", dict(fish_proj=2, fish_fov=240.0)),
    ("BROWN", dict(k1=-0.05, breathing=1.0)),
]


@pytest.mark.parametrize("model,params", CASES, ids=[f"{m}-{i}" for i, (m, _) in enumerate(CASES)])
def test_shader_matches_reference(model, params):
    sc = H.reset(res=RES)
    H.direction_world()
    cam = H.camera(lens=24.0 if model != "FISHEYE" else 10.0, sensor=36.0)
    focus = 0.0
    if "breathing" in params:
        # breathing needs a focus distance; DOF stays off (f-stop huge) so rays stay pinhole
        cam.data.dof.use_dof = True
        cam.data.dof.focus_distance = 0.6
        cam.data.dof.aperture_fstop = 1e6
        focus = 0.6
    _setup_custom_camera(cam, model, params, cam.data.lens)
    img = H.render(f"shader_{model}")
    got = H.decode_dirs(img)
    frame_w = 36.0
    frame_h = 36.0 * RES[1] / RES[0]
    exp, ok, *_ = _expected_dirs(model, params, cam.data.lens, frame_w, frame_h, focus)
    assert ok.mean() > 0.5
    ang = np.degrees(np.arccos(np.clip((got * exp).sum(-1), -1, 1)))
    # Background colour quantisation is float32; allow 0.02° (≈0.03 px at this size).
    assert ang[ok].max() < 0.02, f"max angular error {ang[ok].max():.4f}°"
    # Invalid rays must be black (throughput 0 -> pixel 0 -> decodes to -1 vector)
    if (~ok).any():
        assert np.abs(img[~ok][..., :3]).max() < 1e-6


def test_vignette_matches_reference():
    sc = H.reset(res=RES)
    H.white_world()
    cam = H.camera(lens=24.0)
    params = dict(vig_k1=-0.4, vig_k2=0.1, cos4=0.5)
    _setup_custom_camera(cam, "NONE", params, 24.0)
    img = H.render("vignette")
    exp, ok, st, gx, gy = _expected_dirs("NONE", params, 24.0, 36.0, 36.0 * RES[1] / RES[0])
    v = L.vignette(st, gx, gy, exp[..., 2])
    np.testing.assert_allclose(img[..., 0], v, atol=2e-4)


def test_basic_perspective_equivalence_with_checker():
    """Model NONE must render the same image as Blender's own perspective camera."""
    sc = H.reset(res=(96, 54), samples=8)
    sc.cycles.filter_width = 1.5
    sc.cycles.pixel_filter_type = "BLACKMAN_HARRIS"
    sc.cycles.seed = 3
    H.checker_wall()
    cam = H.camera(lens=35.0)
    ref = H.render("persp_ref")
    _setup_custom_camera(cam, "NONE", {}, 35.0)
    got = H.render("persp_osl")
    assert np.abs(ref[..., :3] - got[..., :3]).mean() < 1e-3
