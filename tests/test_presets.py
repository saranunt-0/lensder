import ast
import math

import pytest

from lensder.core import lens as L
from lensder.core import presets as P
from lensder.core import schema

# Functions Blender's simple-expression evaluator accepts without Python
# (checked on Blender 5.2). ``**`` is NOT simple, so ``pow`` must be used.
SIMPLE_FUNCS = {"clamp", "lerp", "pow", "log", "sqrt", "min", "max"}
SIMPLE_NAMES = {"f", "N", "md", "mv", "mb"}


def _is_simple(expr):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            return False
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in SIMPLE_FUNCS):
            return False
        if isinstance(node, ast.Name) and node.id not in SIMPLE_FUNCS | SIMPLE_NAMES:
            return False
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda)):
            return False
    return True


@pytest.mark.parametrize("lens", P.LENSES, ids=lambda x: x.id)
def test_every_preset_compiles_to_simple_expressions(lens):
    model, exprs = P.compile_expressions(lens)
    assert model in schema.MODEL_BY_ID
    for name, expr in exprs.items():
        assert name in schema.PARAM_BY_NAME, name
        assert _is_simple(expr), expr
        val = P.evaluate(expr, lens.focal_min, lens.max_aperture)
        assert math.isfinite(val)


def _state(lens, focal, body=P.BODY_BY_ID["FULL_FRAME"], fstop=None, **amounts):
    model, params = P.compile_params(lens, focal, fstop or lens.max_aperture, **amounts)
    return L.LensState(model=model, focal=focal, frame_w=body.width * lens.squeeze, frame_h=body.height, params=params)


def test_standard_zoom_goes_from_barrel_to_pincushion():
    z = P.LENS_BY_ID["Z24_70"]
    wide = L.corner_distortion_percent(_state(z, 24))
    tele = L.corner_distortion_percent(_state(z, 70))
    # lensfun-fitted magnitudes: about -3.1% at 24mm and +2.6% at 70mm on full frame
    assert wide == pytest.approx(-3.1, abs=0.4)
    assert tele == pytest.approx(2.6, abs=0.4)


def test_crop_sensor_sees_less_distortion():
    w = P.LENS_BY_ID["W24"]
    ff = L.corner_distortion_percent(_state(w, 24))
    crop = L.corner_distortion_percent(_state(w, 24, body=P.BODY_BY_ID["MFT"]))
    assert abs(crop) < abs(ff)


def test_amount_zero_removes_distortion_and_vignetting():
    w = P.LENS_BY_ID["UW14_MUSTACHE"]
    _, params = P.compile_params(w, 14, 2.8, md=0.0, mv=0.0)
    assert params["k1"] == 0.0 and params["k2"] == 0.0 and params["vig_k1"] == 0.0


def test_vignetting_fades_when_stopping_down():
    lens = P.LENS_BY_ID["P85"]
    open_ = P.compile_params(lens, 85, 1.4)[1]["vig_k1"]
    two_stops = P.compile_params(lens, 85, 2.8)[1]["vig_k1"]
    three_stops = P.compile_params(lens, 85, 4.0)[1]["vig_k1"]
    assert open_ == pytest.approx(2 ** -1.6 - 1)
    assert abs(open_) > abs(two_stops) > 0
    assert three_stops == pytest.approx(0.0, abs=0.02)


def test_anamorphic_presets_have_barrel_and_squeeze():
    for lens in P.LENSES:
        if lens.kind != "ANAMORPHIC":
            continue
        assert lens.squeeze > 1.0
        st = _state(lens, lens.focal_min, body=P.BODY_BY_ID["ALEXA35"])
        assert L.corner_distortion_percent(st) < 0


def test_fisheye_preset_fov():
    lens = P.LENS_BY_ID["FISH8"]
    st = _state(lens, 8)
    # Edge of the 180° circle on an equisolid 8mm: r = 2·8·sin(45°) = 11.31 mm (< 12 mm half-height)
    import numpy as np
    _, _, dz, ok = L.film_to_ray(st, np.array([11.3, 11.4]), np.array([0.0, 0.0]))
    assert ok.tolist() == [True, False]
