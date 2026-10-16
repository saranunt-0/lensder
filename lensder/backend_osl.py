"""Ray-traced lens: Cycles custom OSL camera, driven by Lensder properties.

Every shader parameter gets a driver:

* Advanced mode: ``v`` ← the camera's hidden ID property ``["_lensder_<param>"]``
* Camera Kit mode: the preset's simple expression of ``f`` (focal), ``N`` (f-stop)
  and the amounts ``md``, ``mv``, ``mb`` — all ID properties, see idprops.py

Drivers keep animation (zooms, focus pulls, keyframed coefficients) correct
in the viewport and in final renders, with no frame handlers, and need no
"Auto Run Python Scripts". Drivers only read data that exists without the
add-on, and the compiled bytecode is stored in the .blend, so render farms do
not need Lensder installed (verified by tests/blender/test_addon.py).
Choices such as the model and fisheye projection are written as plain values.
"""

import bpy

from . import idprops
from .core import osl, presets, schema

TEXT_NAME = "lensder_camera.osl"
_PATH = 'cycles_custom["{}"]'


def ensure_text():
    text = bpy.data.texts.get(TEXT_NAME)
    src = osl.source()
    if text is None:
        text = bpy.data.texts.new(TEXT_NAME)
    if text.as_string() != src:
        text.from_string(src)
    return text


def _report(kind, msg):
    if "ERROR" in kind:
        print("Lensder: OSL camera:", msg)


def compile_shader(cam):
    """Compile the shader into the camera (bytecode + parameter properties)."""
    try:
        from cycles import osl as cycles_osl
    except ImportError:
        cycles_osl = None
    if cycles_osl is not None:
        try:
            return bool(cycles_osl.update_custom_camera_shader(cam, _report))
        except Exception as ex:  # OSL disabled in this build, etc.
            print("Lensder: could not compile OSL camera:", ex)
            return False
    # Fallback: re-assigning the shader triggers Cycles' own update.
    text = cam.custom_shader
    cam.custom_shader = None
    cam.custom_shader = text
    return "model" in cam.cycles_custom.keys()


def is_enabled(cam):
    return cam.type == "CUSTOM" and cam.custom_shader is not None and cam.custom_shader.name == TEXT_NAME


def enable(cam):
    st = cam.lensder
    if not hasattr(cam, "cycles_custom"):
        return False  # Cycles add-on disabled
    text = ensure_text()
    if cam.type != "CUSTOM":
        st.original_type = cam.type
        cam.type = "CUSTOM"
    if cam.custom_mode != "INTERNAL":
        cam.custom_mode = "INTERNAL"
    src_hash = osl.source_hash()
    needs_compile = (cam.custom_shader != text or st.shader_hash != src_hash
                     or "model" not in cam.cycles_custom.keys() or not cam.custom_bytecode)
    if needs_compile:
        if cam.custom_shader != text:
            cam.custom_shader = text  # Cycles compiles on assignment
        if "model" not in cam.cycles_custom.keys() or st.shader_hash != src_hash:
            if not compile_shader(cam):
                return False
        st.shader_hash = src_hash
        st.driver_signature = ""
    sig = signature(cam)
    if st.driver_signature != sig or not _drivers_present(cam):
        build_drivers(cam)
        st.driver_signature = sig
    store_static_values(cam)
    return True


def store_static_values(cam):
    """Write every non-driven shader parameter: the model, the fisheye
    projection, and the constants of Camera Kit presets."""
    from . import sync

    cc = cam.cycles_custom
    model, params = sync.model_and_params(cam)
    values = {"model": schema.MODEL_BY_ID[model].index, "focal_length": sync.focal(cam)}
    values.update(params)
    driven = _driven(cam)
    for name, value in values.items():
        if name not in cc.keys() or _PATH.format(name) in driven:
            continue
        is_int = name == "model" or (name in schema.PARAM_BY_NAME and schema.PARAM_BY_NAME[name].is_int)
        value = int(value) if is_int else float(value)
        if cc[name] != value:
            cc[name] = value


def disable(cam):
    remove_drivers(cam)
    st = cam.lensder
    st.driver_signature = ""
    if is_enabled(cam):
        cam.type = st.original_type if st.original_type in ("PERSP", "ORTHO", "PANO") else "PERSP"


def signature(cam):
    st = cam.lensder
    if st.mode == "PRESET":
        return f"PRESET:{st.lens_preset}:{osl.source_hash()}"
    return f"ADVANCED:{osl.source_hash()}"


def _driven(cam):
    ad = cam.animation_data
    return set() if ad is None else {fc.data_path for fc in ad.drivers}


def _drivers_present(cam):
    ad = cam.animation_data
    if ad is None:
        return False
    return any(fc.data_path == _PATH.format("focal_length") for fc in ad.drivers)


def remove_drivers(cam):
    ad = cam.animation_data
    if ad is None:
        return
    for fc in list(ad.drivers):
        if fc.data_path.startswith("cycles_custom["):
            ad.drivers.remove(fc)


def _add_driver(cam, name, expression, variables):
    fc = cam.driver_add(_PATH.format(name))
    for mod in list(fc.modifiers):
        fc.modifiers.remove(mod)
    drv = fc.driver
    drv.type = "SCRIPTED"
    for var in list(drv.variables):
        drv.variables.remove(var)
    for var_name, data_path in variables:
        v = drv.variables.new()
        v.name = var_name
        v.type = "SINGLE_PROP"
        v.targets[0].id_type = "CAMERA"
        v.targets[0].id = cam
        v.targets[0].data_path = data_path
    drv.expression = expression
    return fc


_PRESET_VARS = {
    "f": idprops.path("focal"),
    "N": idprops.path("fstop"),
    "md": idprops.path("amount_distortion"),
    "mv": idprops.path("amount_vignette"),
    "mb": idprops.path("amount_breathing"),
}


def build_drivers(cam):
    remove_drivers(cam)
    cc = cam.cycles_custom
    st = cam.lensder
    _add_driver(cam, "focal_length", "f", [("f", idprops.path("focal"))])

    idprops.ensure(cam)
    if st.mode == "PRESET":
        model, exprs = presets.compile_expressions(presets.LENS_BY_ID[st.lens_preset])
        cc["model"] = schema.MODEL_BY_ID[model].index
        for p in schema.ALL_PARAMS:
            expr = exprs.get(p.name)
            if expr is None:
                cc[p.name] = int(p.default) if p.is_int else float(p.default)
                continue
            used = [(n, path) for n, path in _PRESET_VARS.items() if _uses(expr, n)]
            if not used:  # constant
                value = presets.evaluate(expr, 0.0, 0.0)
                cc[p.name] = int(value) if p.is_int else value
            else:
                _add_driver(cam, p.name, expr, used)
        return

    for p in idprops.FLOAT_PARAMS:
        _add_driver(cam, p.name, "v", [("v", idprops.path(p.name))])


def _uses(expr, name):
    import re
    return re.search(rf"\b{re.escape(name)}\b", expr) is not None


def refresh_all_shaders():
    """After an add-on update, rebuild the shader text and recompile every camera using it."""
    if bpy.data.texts.get(TEXT_NAME) is None:
        return
    ensure_text()
    for cam in bpy.data.cameras:
        if getattr(cam, "lensder", None) and is_enabled(cam) and cam.lensder.shader_hash != osl.source_hash():
            if compile_shader(cam):
                cam.lensder.shader_hash = osl.source_hash()
                build_drivers(cam)
                cam.lensder.driver_signature = signature(cam)
