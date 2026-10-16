"""Blender properties stored on every camera (``Camera.lensder``)."""

import zlib

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from .core import presets, schema


def _stable_number(identifier):
    """Enum values are saved in .blend files as numbers. Deriving them from the
    identifier keeps saved files valid when presets are added or reordered."""
    return zlib.crc32(identifier.encode()) & 0x7FFFFFFF


def _body_items():
    items = [(b.id, b.label, b.description, "", _stable_number(b.id)) for b in presets.BODIES]
    items.append(("CUSTOM", "Custom Sensor", "Enter the sensor size yourself", "", _stable_number("CUSTOM")))
    return items


def _lens_items():
    items = []
    groups = (("SPHERICAL", "Spherical"), ("ANAMORPHIC", "Anamorphic"), ("FISHEYE", "Fisheye"))
    for kind, heading in groups:
        items.append(("", heading, ""))
        for lens in presets.LENSES:
            if lens.kind == kind:
                items.append((lens.id, lens.label, lens.description, "", _stable_number(lens.id)))
    return items


_BODY_ITEMS = _body_items()
_LENS_ITEMS = _lens_items()
_MODEL_ITEMS = [(m.id, m.label, m.description, "", m.index) for m in schema.MODELS]
_PROJECTION_ITEMS = [(pid, label, desc, "", i) for i, (pid, label, desc) in enumerate(schema.FISHEYE_PROJECTIONS)]


def _sync(self, context):
    from . import sync
    sync.on_settings_changed(self, context)


def _sync_preset(self, context):
    from . import sync
    sync.on_preset_changed(self, context)


def _sync_mode(self, context):
    from . import sync
    sync.on_mode_changed(self, context)


def _sync_toggle(name):
    def update(self, context):
        from . import sync
        sync.on_scene_toggle(self, context, name)
    return update


class LensderCameraSettings(bpy.types.PropertyGroup):
    # Annotations for the schema-driven model parameters are added below the class.
    mode: EnumProperty(
        name="Mode",
        items=(
            ("NATIVE", "Blender", "Standard Blender camera; Lensder does nothing", "CAMERA_DATA", 0),
            ("PRESET", "Camera Kit", "Pick a real camera body and lens; simple controls", "OUTLINER_OB_CAMERA", 1),
            ("ADVANCED", "Advanced", "Full lens model with every coefficient", "PREFERENCES", 2),
        ),
        default="NATIVE",
        update=_sync_mode,
    )
    method: EnumProperty(
        name="Distortion Method",
        items=(
            ("AUTO", "Automatic", "Ray-traced when Cycles can (CPU/OptiX), otherwise post-process", "AUTO", 0),
            ("RAYTRACE", "Ray-Traced (Cycles)", "Custom OSL camera: exact, no overscan; Cycles CPU or OptiX only",
             "SHADING_RENDERED", 1),
            ("COMPOSITOR", "Post-Process", "Compositor ST-map: works with EEVEE and every GPU; renders with overscan",
             "NODE_COMPOSITING", 2),
            ("NONE", "Optics Only", "No distortion or vignetting; sensor, focal length, focus and exposure only",
             "CANCEL", 3),
        ),
        default="AUTO",
        update=_sync,
    )

    # --- Body -------------------------------------------------------------
    body: EnumProperty(name="Camera Body", items=_BODY_ITEMS, default="FULL_FRAME", update=_sync)
    sensor_w: FloatProperty(name="Sensor Width", default=36.0, min=1.0, soft_max=60.0,
                            subtype="DISTANCE_CAMERA", unit="CAMERA", update=_sync)
    sensor_h: FloatProperty(name="Sensor Height", default=24.0, min=1.0, soft_max=40.0,
                            subtype="DISTANCE_CAMERA", unit="CAMERA", update=_sync)
    squeeze: FloatProperty(name="Anamorphic Squeeze", default=1.0, min=0.5, soft_max=2.0, max=4.0,
                           description="Horizontal compression of an anamorphic lens (1 = spherical)",
                           update=_sync)

    # --- Lens preset -------------------------------------------------------
    lens_preset: EnumProperty(name="Lens", items=_LENS_ITEMS, default="N50", update=_sync_preset)
    # Distortion / vignetting / breathing amounts are animatable ID properties: see idprops.py

    # --- Post effects (compositor) ----------------------------------------
    chromatic: FloatProperty(name="Chromatic Aberration", default=0.0, min=0.0, max=1.0, subtype="FACTOR",
                             description="Lateral colour fringing toward the edges (compositor)", update=_sync)
    flare: BoolProperty(name="Streak Flares", default=False,
                        description="Horizontal anamorphic streak flares on bright highlights (compositor)",
                        update=_sync)
    flare_strength: FloatProperty(name="Flare Strength", default=0.3, min=0.0, max=1.0, subtype="FACTOR",
                                  update=_sync)

    # --- Advanced model ------------------------------------------------------
    model: EnumProperty(name="Distortion Model", items=_MODEL_ITEMS, default="BROWN", update=_sync)
    fish_proj: EnumProperty(name="Projection", items=_PROJECTION_ITEMS, default="EQUISOLID",
                            description=schema.PARAM_BY_NAME["fish_proj"].description, update=_sync)
    # Model coefficients are animatable ID properties: see idprops.py

    # --- Exposure ------------------------------------------------------------
    use_exposure: BoolProperty(name="Control Exposure", default=False,
                               description="Drive the scene exposure from this camera",
                               update=_sync_toggle("exposure"))
    exposure_mode: EnumProperty(
        name="Exposure",
        items=(("AUTO", "Auto", "Aperture, shutter and ISO do not change brightness (like auto exposure)"),
               ("MANUAL", "Manual", "Exposure triangle: brightness follows aperture, shutter and ISO, "
                                    "relative to the metered reference")),
        default="AUTO", update=_sync)
    iso: FloatProperty(name="ISO", default=100.0, min=1.0, soft_min=50.0, soft_max=25600.0, update=_sync)
    ev_comp: FloatProperty(name="Exposure Compensation", default=0.0, soft_min=-5.0, soft_max=5.0,
                           description="Additional exposure in stops", update=_sync)
    ref_fstop: FloatProperty(name="Reference f-stop", default=2.8, min=0.5, update=_sync)
    ref_shutter_inv: FloatProperty(name="Reference Shutter 1/x", default=50.0, min=0.0001, update=_sync)
    ref_iso: FloatProperty(name="Reference ISO", default=100.0, min=1.0, update=_sync)

    # --- Shutter ---------------------------------------------------------------
    use_shutter: BoolProperty(name="Control Motion Blur", default=False,
                              description="Drive motion blur from the shutter speed / angle",
                              update=_sync_toggle("shutter"))
    shutter_mode: EnumProperty(
        name="Shutter",
        items=(("SPEED", "Speed", "Stills convention: 1/x seconds"),
               ("ANGLE", "Angle", "Cinema convention: degrees of a rotary shutter (180° = half a frame)")),
        default="ANGLE", update=_sync)
    shutter_inv: FloatProperty(name="Shutter 1/x", default=50.0, min=0.0001, soft_min=1.0, soft_max=8000.0,
                               description="Shutter speed as 1/x seconds (50 = 1/50 s)", update=_sync)
    shutter_angle: FloatProperty(name="Shutter Angle", default=180.0, min=0.1, max=360.0 * 8, soft_max=360.0,
                                 update=_sync)

    # --- White balance --------------------------------------------------------
    use_white_balance: BoolProperty(name="Control White Balance", default=False,
                                    description="Drive the scene white balance from this camera",
                                    update=_sync_toggle("white_balance"))
    wb_temperature: FloatProperty(name="Temperature", default=6500.0, min=1800.0, max=100000.0, soft_max=12000.0,
                                  subtype="COLOR_TEMPERATURE",
                                  description="Camera white balance setting. Lower values make the image bluer "
                                              "(e.g. 3200 K neutralises tungsten light)", update=_sync)
    wb_tint: FloatProperty(name="Tint", default=0.0, soft_min=-100.0, soft_max=100.0,
                           description="Green (−) to magenta (+) correction", update=_sync)

    # --- Viewport ----------------------------------------------------------------
    show_overlay: BoolProperty(name="Lens Grid Overlay", default=True,
                               description="Draw the distorted grid and frame in camera view", update=_sync)
    overlay_density: bpy.props.IntProperty(name="Grid Lines", default=8, min=2, max=32, update=_sync)

    # --- Internal state ---------------------------------------------------------
    original_type: StringProperty(default="PERSP", options={"HIDDEN"})
    last_mode: StringProperty(default="NATIVE", options={"HIDDEN"})
    configured: BoolProperty(default=False, options={"HIDDEN"})
    driver_signature: StringProperty(default="", options={"HIDDEN"})
    shader_hash: StringProperty(default="", options={"HIDDEN"})
    overscan: FloatProperty(default=1.0, options={"HIDDEN"})


def register():
    bpy.utils.register_class(LensderCameraSettings)
    bpy.types.Camera.lensder = bpy.props.PointerProperty(type=LensderCameraSettings)


def unregister():
    del bpy.types.Camera.lensder
    bpy.utils.unregister_class(LensderCameraSettings)
