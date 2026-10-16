"""Animatable lens values, stored as hidden ID properties on the Camera datablock.

Why not ``bpy.props``? The OSL shader parameters are driven. A driver whose
target cannot be resolved evaluates to 0.0 and overwrites the stored value
(verified on Blender 5.2). Add-on (``bpy.props``) properties do not exist when
a file is opened without Lensder, for example on a render farm, so every
coefficient would silently become zero.

ID properties on the camera are part of the .blend and can be keyframed. A
leading underscore hides them from the Custom Properties panel. Drivers that
read them work everywhere.
"""

from .core import schema

PREFIX = "_lensder_"

AMOUNTS = (
    ("amount_distortion", "Distortion", "Scales the lens' characteristic distortion"),
    ("amount_vignette", "Vignetting", "Scales the lens' corner darkening (fades when stopping down)"),
    ("amount_breathing", "Focus Breathing", "Scales how much the framing changes with focus"),
)

# Integer parameters are choices (projection), not animatable; they live in bpy.props enums.
FLOAT_PARAMS = tuple(p for p in schema.ALL_PARAMS if not p.is_int)

# While Lensder is active it owns focal length and f-stop: Blender's ``lens`` and
# ``dof.aperture_fstop`` are driven from these. Shader drivers then read only ID
# properties, never native camera properties. Reading those would create a
# dependency cycle (the camera's parameters depend on its own driven
# properties), and Blender warns about it on every relations update.
OPTICS = (
    ("focal", "lens", "Focal Length", "Focal length of the lens (mm); for zooms, limited to the lens' range"),
    ("fstop", "dof.aperture_fstop", "Aperture f/", "f-number: smaller = more background blur (and brighter in "
                                                   "manual exposure)"),
)


def key(name):
    return PREFIX + name


def path(name):
    return f'["{key(name)}"]'


def ensure(cam):
    """Create missing properties with defaults and UI metadata."""
    if key("focal") not in cam.keys():
        cam[key("focal")] = float(cam.lens)
        cam.id_properties_ui(key("focal")).update(min=1.0, max=5000.0, soft_min=4.0, soft_max=300.0,
                                                  subtype="DISTANCE_CAMERA", precision=1, description=OPTICS[0][3])
    if key("fstop") not in cam.keys():
        cam[key("fstop")] = float(cam.dof.aperture_fstop)
        cam.id_properties_ui(key("fstop")).update(min=0.5, max=128.0, soft_min=0.95, soft_max=22.0,
                                                  precision=1, step=10, description=OPTICS[1][3])
    for p in FLOAT_PARAMS:
        k = key(p.name)
        if k not in cam.keys():
            cam[k] = float(p.default)
            ui = cam.id_properties_ui(k)
            kw = dict(default=float(p.default), soft_min=p.soft_min, soft_max=p.soft_max,
                      description=p.description, precision=p.precision, step=1)
            if p.unit == "mm":
                kw["subtype"] = "DISTANCE_CAMERA"
            ui.update(**kw)
    for name, _label, desc in AMOUNTS:
        k = key(name)
        if k not in cam.keys():
            cam[k] = 1.0
            cam.id_properties_ui(k).update(default=1.0, min=0.0, max=5.0, soft_min=0.0, soft_max=2.0,
                                           description=desc, subtype="FACTOR", precision=2)


def get(cam, name):
    v = cam.get(key(name))
    if v is None:
        p = schema.PARAM_BY_NAME.get(name)
        return float(p.default) if p is not None else 1.0
    return float(v)


def set(cam, name, value):
    k = key(name)
    if cam.get(k) != value:
        cam[k] = float(value)
        cam.update_tag()  # Python ID-property writes do not tag the depsgraph by themselves


def set_range(cam, name, lo, hi):
    """UI limits (e.g. a zoom lens' range or its widest aperture)."""
    cam.id_properties_ui(key(name)).update(min=lo, max=hi, soft_min=lo, soft_max=hi)


def link_optics(cam):
    """Drive Blender's lens / f-stop from Lensder's (no-op if already linked)."""
    ensure(cam)
    for name, native, _label, _desc in OPTICS:
        if _find_driver(cam, native) is not None:
            continue
        fc = cam.driver_add(native)
        for mod in list(fc.modifiers):
            fc.modifiers.remove(mod)
        drv = fc.driver
        drv.type = "AVERAGE"
        for var in list(drv.variables):
            drv.variables.remove(var)
        var = drv.variables.new()
        var.name = "v"
        var.type = "SINGLE_PROP"
        var.targets[0].id_type = "CAMERA"
        var.targets[0].id = cam
        var.targets[0].data_path = path(name)


def unlink_optics(cam):
    """Remove the drivers and leave Blender's own values at Lensder's current ones."""
    for name, native, _label, _desc in OPTICS:
        fc = _find_driver(cam, native)
        if fc is not None:
            cam.animation_data.drivers.remove(fc)
            if key(name) in cam.keys():
                owner, attr = (cam.dof, "aperture_fstop") if native.startswith("dof.") else (cam, native)
                setattr(owner, attr, get(cam, name))


def is_linked(cam):
    return _find_driver(cam, "lens") is not None


def _find_driver(cam, data_path):
    ad = cam.animation_data
    if ad is None:
        return None
    return next((fc for fc in ad.drivers if fc.data_path == data_path), None)


def label(name):
    p = schema.PARAM_BY_NAME.get(name)
    if p is not None:
        return p.label + (" (°)" if p.unit == "deg" else "")
    for n, _native, lbl, _ in OPTICS:
        if n == name:
            return lbl
    return next(lbl for n, lbl, _ in AMOUNTS if n == name)
