"""User interface: a 3D-View sidebar tab and a Camera Properties panel with the same content."""

import bpy

from . import idprops, sync
from .core import schema

MODEL_PARAMS = {m.id: m.params for m in schema.MODELS}
_PARAM = schema.PARAM_BY_NAME


def _cam_object(context):
    ob = getattr(context, "object", None) if getattr(context, "space_data", None) and \
        context.space_data.type == "PROPERTIES" else context.active_object
    if ob is not None and ob.type == "CAMERA":
        return ob
    return None


def _fmt_m(d):
    if d == float("inf"):
        return "∞"
    return f"{d:.2f} m" if d < 10 else f"{d:.1f} m"


# ---------------------------------------------------------------------------
# Section drawers (shared by both editors)
# ---------------------------------------------------------------------------

def draw_main(layout, context, ob):
    cam = ob.data
    st = cam.lensder
    scene = context.scene
    layout.row().prop(st, "mode", expand=True)
    if st.mode == "NATIVE":
        layout.label(text="Standard Blender camera. Pick Camera Kit or Advanced", icon="INFO")
        layout.label(text="to simulate a real camera body and lens.")
        return
    info = sync.readouts(ob, scene)
    box = layout.box()
    path = info["backend"]
    if path == "RAYTRACE":
        box.label(text="Ray-traced lens (Cycles OSL camera)", icon="SHADING_RENDERED")
    elif path == "COMPOSITOR":
        box.label(text=f"Post-process lens (compositor) · overscan {info['overscan']:.3f}×", icon="NODE_COMPOSITING")
    else:
        box.label(text="Optics only (no distortion)", icon="CAMERA_DATA")
    for w in sync.warnings(ob, scene):
        box.label(text=w, icon="ERROR")
    if scene.camera != ob:
        box.operator("lensder.make_active", icon="VIEW_CAMERA")
    row = box.row(align=True)
    row.operator("lensder.viewport_preview", icon="HIDE_OFF", text="Preview in Viewport")


def draw_body(layout, context, ob):
    cam = ob.data
    st = cam.lensder
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(st, "body")
    if st.body == "CUSTOM":
        col = layout.column(align=True)
        col.prop(st, "sensor_w", text="Width")
        col.prop(st, "sensor_h", text="Height")
    if st.mode == "ADVANCED":
        layout.prop(st, "squeeze")
    info = sync.readouts(ob, context.scene)
    col = layout.column(align=True)
    fw, fh = info["frame"]
    col.label(text=f"Frame {fw:.2f} × {fh:.2f} mm (desqueezed) · crop {info['crop']:.2f}×")
    col.label(text=f"Field of view {info['hfov']:.1f}° × {info['vfov']:.1f}°")


def draw_lens(layout, context, ob):
    cam = ob.data
    st = cam.lensder
    layout.use_property_split = True
    layout.use_property_decorate = True
    if st.mode == "PRESET":
        layout.prop(st, "lens_preset")
    rng = sync.zoom_range(cam)
    label = "Focal Length"
    if rng is not None:
        label = f"Zoom {rng[0]:g}–{rng[1]:g}" if rng[1] > rng[0] else f"Focal (prime {rng[0]:g})"
    _idprop(layout, cam, "focal", text=label)
    _idprop(layout, cam, "fstop")
    layout.prop(cam.dof, "use_dof", text="Depth of Field")
    col = layout.column()
    col.active = cam.dof.use_dof
    col.prop(cam.dof, "focus_object", text="Focus On")
    sub = col.column()
    sub.active = cam.dof.focus_object is None
    sub.prop(cam.dof, "focus_distance", text="Focus Distance")
    if st.mode == "ADVANCED":
        col.prop(cam.dof, "aperture_blades", text="Blades")
        col.prop(cam.dof, "aperture_ratio", text="Bokeh Ratio")
    info = sync.readouts(ob, context.scene)
    out = layout.column(align=True)
    if "dof" in info:
        near, far, hyper = info["dof"]
        out.label(text=f"Sharp from {_fmt_m(near)} to {_fmt_m(far)} (hyperfocal {_fmt_m(hyper)})", icon="RESTRICT_VIEW_OFF")
    if info["distortion"] is not None and abs(info["distortion"]) > 0.005:
        kind = "barrel" if info["distortion"] < 0 else "pincushion"
        out.label(text=f"Corner distortion {info['distortion']:+.2f}% ({kind})")
    if abs(info["focal_eff"] - sync.focal(cam)) > 0.01:
        out.label(text=f"Breathing: frames like {info['focal_eff']:.1f} mm at this focus")


def _idprop(layout, cam, name, text=None, **kw):
    if idprops.key(name) in cam.keys():
        layout.prop(cam, idprops.path(name), text=text or idprops.label(name), **kw)


def draw_character(layout, context, ob):
    cam = ob.data
    st = cam.lensder
    layout.use_property_split = True
    layout.use_property_decorate = True
    col = layout.column()
    col.active = st.method != "NONE"
    for name, _label, _desc in idprops.AMOUNTS:
        _idprop(col, cam, name, slider=True)
    lens = sync.lens_preset(st)
    traits = []
    if lens.chromatic >= 0.3:
        traits.append("colour fringing")
    if lens.flare:
        traits.append("streak flares")
    if traits:
        layout.label(text="Typical for this lens: " + ", ".join(traits), icon="LIGHT")
    _draw_post(layout, st)


def _draw_post(layout, st):
    layout.separator()
    layout.prop(st, "chromatic", slider=True)
    row = layout.row(heading="Streak Flares")
    row.prop(st, "flare", text="")
    sub = row.row()
    sub.active = st.flare
    sub.prop(st, "flare_strength", text="")


def draw_model(layout, context, ob):
    cam = ob.data
    st = cam.lensder
    layout.use_property_split = True
    layout.use_property_decorate = True
    layout.prop(st, "model")
    params = MODEL_PARAMS.get(st.model, ())
    if params:
        col = layout.column(align=True)
        for name in params:
            if name == "fish_proj":
                col.prop(st, "fish_proj")
            else:
                _idprop(col, cam, name)
    layout.label(text="Common")
    col = layout.column(align=True)
    for name in ("center_x", "center_y"):
        _idprop(col, cam, name)
    col = layout.column(align=True)
    for name in ("breathing", "vig_k1", "vig_k2", "vig_k3", "cos4"):
        _idprop(col, cam, name)
    _draw_post(layout, st)
    layout.operator("lensder.from_movieclip", icon="TRACKER")


def draw_exposure_header(layout, ob):
    layout.prop(ob.data.lensder, "use_exposure", text="")


def draw_exposure(layout, context, ob):
    cam = ob.data
    st = cam.lensder
    layout.use_property_split = True
    layout.use_property_decorate = True
    layout.active = st.use_exposure
    layout.row().prop(st, "exposure_mode", expand=True)
    if st.exposure_mode == "MANUAL":
        _idprop(layout, cam, "fstop")
        if st.shutter_mode == "ANGLE":
            layout.prop(st, "shutter_angle")
        else:
            layout.prop(st, "shutter_inv", text="Shutter 1/x")
        layout.prop(st, "iso")
        row = layout.row()
        row.operator("lensder.meter", icon="LIGHT_SUN")
        stops = 0.0
        if st.use_exposure:
            from .core import exposure as E
            stops = E.relative_stops(sync.fstop(cam), sync.shutter_seconds(st, context.scene), st.iso,
                                     st.ref_fstop, 1.0 / st.ref_shutter_inv, st.ref_iso)
        layout.label(text=f"{stops:+.2f} stops vs. metered reference")
    layout.prop(st, "ev_comp")
    info = sync.readouts(ob, context.scene)
    if "ev100" in info:
        layout.label(text=f"EV₁₀₀ {info['ev100']:.1f}")


def draw_shutter_header(layout, ob):
    layout.prop(ob.data.lensder, "use_shutter", text="")


def draw_shutter(layout, context, ob):
    st = ob.data.lensder
    layout.use_property_split = True
    layout.use_property_decorate = True
    layout.active = st.use_shutter
    layout.row().prop(st, "shutter_mode", expand=True)
    if st.shutter_mode == "ANGLE":
        layout.prop(st, "shutter_angle")
    else:
        layout.prop(st, "shutter_inv", text="Shutter 1/x")
    sec = sync.shutter_seconds(st, context.scene)
    from .core import exposure as E
    layout.label(text=f"{E.format_shutter(sec)} s · {E.shutter_angle_from_seconds(sec, sync.fps(context.scene)):.0f}° "
                      f"at {sync.fps(context.scene):g} fps")


def draw_wb_header(layout, ob):
    layout.prop(ob.data.lensder, "use_white_balance", text="")


def draw_wb(layout, context, ob):
    st = ob.data.lensder
    layout.use_property_split = True
    layout.use_property_decorate = True
    layout.active = st.use_white_balance
    layout.prop(st, "wb_temperature")
    layout.prop(st, "wb_tint")
    row = layout.row(align=True)
    for k, label in ((3200, "Tungsten"), (4300, "Fluorescent"), (5600, "Daylight"), (6500, "Overcast")):
        op = row.operator("wm.context_set_float", text=label)
        op.data_path = "object.data.lensder.wb_temperature" if context.space_data.type == "PROPERTIES" \
            else "active_object.data.lensder.wb_temperature"
        op.value = float(k)


def draw_viewport(layout, context, ob):
    st = ob.data.lensder
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(st, "method")
    row = layout.row(heading="Overlay")
    row.prop(st, "show_overlay", text="")
    sub = row.row()
    sub.active = st.show_overlay
    sub.prop(st, "overlay_density", text="Lines")
    row = layout.row(align=True)
    row.operator("lensder.export_stmap", icon="EXPORT")
    row.operator("lensder.refresh", icon="FILE_REFRESH")


# ---------------------------------------------------------------------------
# Panel classes (generated for both editors)
# ---------------------------------------------------------------------------

SECTIONS = (
    # id, label, drawer, header drawer, modes, default closed
    ("BODY", "Camera Body", draw_body, None, {"PRESET", "ADVANCED"}, False),
    ("LENS", "Lens", draw_lens, None, {"PRESET", "ADVANCED"}, False),
    ("CHARACTER", "Lens Character", draw_character, None, {"PRESET"}, False),
    ("MODEL", "Lens Model", draw_model, None, {"ADVANCED"}, False),
    ("EXPOSURE", "Exposure", draw_exposure, draw_exposure_header, {"PRESET", "ADVANCED"}, True),
    ("SHUTTER", "Shutter & Motion Blur", draw_shutter, draw_shutter_header, {"PRESET", "ADVANCED"}, True),
    ("WB", "White Balance", draw_wb, draw_wb_header, {"PRESET", "ADVANCED"}, True),
    ("VIEWPORT", "Method & Viewport", draw_viewport, None, {"PRESET", "ADVANCED"}, True),
)


def _make_panels(prefix, space, region, extra):
    classes = []

    class Main(bpy.types.Panel):
        bl_idname = f"{prefix}_PT_lensder"
        bl_label = "Lensder"
        bl_space_type = space
        bl_region_type = region

        @classmethod
        def poll(cls, context):
            ob = _cam_object(context)
            return ob is not None and getattr(ob.data, "lensder", None) is not None

        def draw(self, context):
            draw_main(self.layout, context, _cam_object(context))

    for k, v in extra.items():
        setattr(Main, k, v)
    Main.__name__ = f"{prefix}_PT_lensder"
    classes.append(Main)

    for sid, label, drawer, header, modes, closed in SECTIONS:
        def make(sid=sid, label=label, drawer=drawer, header=header, modes=modes, closed=closed):
            attrs = {
                "bl_idname": f"{prefix}_PT_lensder_{sid.lower()}",
                "bl_label": label,
                "bl_space_type": space,
                "bl_region_type": region,
                "bl_parent_id": Main.bl_idname,
                "bl_options": {"DEFAULT_CLOSED"} if closed else set(),
                "poll": classmethod(lambda cls, context: (
                    Main.poll(context) and _cam_object(context).data.lensder.mode in modes)),
                "draw": lambda self, context: drawer(self.layout, context, _cam_object(context)),
            }
            if header is not None:
                attrs["draw_header"] = lambda self, context: header(self.layout, _cam_object(context))
            attrs.update({k: v for k, v in extra.items() if k == "bl_category"})
            return type(f"{prefix}_PT_lensder_{sid.lower()}", (bpy.types.Panel,), attrs)
        classes.append(make())
    return classes


PANELS = (
    _make_panels("VIEW3D", "VIEW_3D", "UI", {"bl_category": "Lensder"})
    + _make_panels("DATA", "PROPERTIES", "WINDOW", {"bl_context": "data"})
)
