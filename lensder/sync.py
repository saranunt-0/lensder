"""Applies Lensder settings to Blender cameras and scenes.

This is the only module that writes Blender camera and scene state. Every
write goes through ``_set``, which skips writes that would not change
anything, so handlers that call ``apply`` repeatedly do not cause depsgraph
update loops.
"""

import math

import bpy

from . import idprops
from .core import exposure as E
from .core import lens as L
from .core import optics, presets, schema
from .core import stmap as S

_BUSY = False


def _set(obj, attr, value, tol=1e-7):
    cur = getattr(obj, attr)
    if isinstance(value, float) and isinstance(cur, float):
        if abs(cur - value) <= tol * max(1.0, abs(value)):
            return False
    elif cur == value:
        return False
    setattr(obj, attr, value)
    return True


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def is_active(cam):
    return cam is not None and getattr(cam, "lensder", None) is not None and cam.lensder.mode != "NATIVE"


def lens_preset(settings):
    return presets.LENS_BY_ID.get(settings.lens_preset, presets.LENS_BY_ID["N50"])


def sensor_size(settings):
    if settings.body == "CUSTOM":
        return settings.sensor_w, settings.sensor_h
    body = presets.BODY_BY_ID[settings.body]
    return body.width, body.height


def squeeze(settings):
    if settings.mode == "PRESET":
        return lens_preset(settings).squeeze
    return settings.squeeze


def render_aspect(scene):
    r = scene.render
    return (r.resolution_x * r.pixel_aspect_x) / max(r.resolution_y * r.pixel_aspect_y, 1e-9)


def gate_fit(cam, scene, overscan=1.0):
    w, h = sensor_size(cam.lensder)
    return optics.fit_gate(w, h, squeeze(cam.lensder), render_aspect(scene), overscan)


def focal(cam):
    """Focal length (mm): Lensder's own while active, Blender's otherwise."""
    if is_active(cam) and idprops.key("focal") in cam.keys():
        return idprops.get(cam, "focal")
    return cam.lens


def fstop(cam):
    if is_active(cam) and idprops.key("fstop") in cam.keys():
        return idprops.get(cam, "fstop")
    return cam.dof.aperture_fstop


def amounts(cam):
    return tuple(idprops.get(cam, n) for n, _, _ in idprops.AMOUNTS)


def projection_index(settings):
    return [i[0] for i in schema.FISHEYE_PROJECTIONS].index(settings.fish_proj)


def model_and_params(cam):
    """Effective (model id, parameter dict), exactly what the shader receives."""
    st = cam.lensder
    if st.mode == "PRESET":
        return presets.compile_params(lens_preset(st), focal(cam), fstop(cam), *amounts(cam))
    params = schema.default_params()
    for p in idprops.FLOAT_PARAMS:
        params[p.name] = idprops.get(cam, p.name)
    params["fish_proj"] = projection_index(st)
    return st.model, params


def focus_distance(cam_ob):
    cam = cam_ob.data
    if not cam.dof.use_dof:
        return 0.0
    if cam.dof.focus_object is not None:
        d = cam_ob.matrix_world.inverted() @ cam.dof.focus_object.matrix_world.translation
        return max(-d.z, 1e-4)
    return cam.dof.focus_distance


def lens_state(cam_ob, scene):
    """LensState for the camera's recorded (non-overscanned) frame."""
    cam = cam_ob.data
    fit = gate_fit(cam, scene)
    model, params = model_and_params(cam)
    if cam.lensder.method == "NONE":
        model = "NONE"
        params = schema.default_params()
    return L.LensState(model=model, focal=focal(cam), frame_w=fit.frame_w, frame_h=fit.frame_h,
                       focus_distance=focus_distance(cam_ob), params=params)


def has_lens_effect(state):
    if state.model != "NONE":
        return True
    p = state.params
    return any(p[k] != 0.0 for k in ("vig_k1", "vig_k2", "vig_k3", "cos4", "breathing"))


def raytrace_supported(scene):
    """Cycles OSL cameras run on CPU and OptiX only (see docs/research.md)."""
    if scene.render.engine != "CYCLES":
        return False, "Ray-traced lenses need Cycles"
    cyc = getattr(scene, "cycles", None)
    if cyc is None or cyc.device == "CPU":
        return True, ""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        backend = prefs.compute_device_type
    except (KeyError, AttributeError):
        backend = "NONE"
    if backend in ("NONE", "OPTIX"):
        return True, ""
    return False, f"OSL cameras are not supported on {backend}; use CPU or OptiX"


def backend(cam_ob, scene):
    """Which distortion path this camera uses: 'RAYTRACE', 'COMPOSITOR' or 'NONE'."""
    st = cam_ob.data.lensder
    if st.mode == "NATIVE" or st.method == "NONE":
        return "NONE"
    if st.method == "RAYTRACE":
        return "RAYTRACE"
    if st.method == "COMPOSITOR":
        return "COMPOSITOR"
    ok, _ = raytrace_supported(scene)
    return "RAYTRACE" if ok else "COMPOSITOR"


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply(cam_ob, scene=None, rebake=True):
    """Make Blender match the camera's Lensder settings."""
    global _BUSY
    if _BUSY or cam_ob is None or cam_ob.type != "CAMERA":
        return
    scene = scene or bpy.context.scene
    _BUSY = True
    try:
        _apply(cam_ob, scene, rebake)
    finally:
        _BUSY = False


def _apply(cam_ob, scene, rebake):
    from . import backend_comp, backend_osl

    cam = cam_ob.data
    st = cam.lensder
    is_scene_cam = scene.camera == cam_ob

    if st.mode != "NATIVE":
        idprops.link_optics(cam)
    if st.mode == "NATIVE":
        idprops.unlink_optics(cam)
        backend_osl.disable(cam)
        if st.overscan != 1.0:
            # Undo the compositor overscan so the camera frames as before.
            _set(cam, "sensor_width", cam.sensor_width / st.overscan)
            _set(cam, "sensor_height", cam.sensor_height / st.overscan)
            st.overscan = 1.0
        if is_scene_cam:
            backend_comp.disable(scene)
        return

    path = backend(cam_ob, scene)
    state = lens_state(cam_ob, scene)
    effect = has_lens_effect(state)

    overscan = 1.0
    if path == "COMPOSITOR" and effect and is_scene_cam:
        overscan = stable_overscan(cam_ob, scene, state)

    fit = optics.fit_gate(*sensor_size(st), squeeze(st), render_aspect(scene), overscan)
    _set(cam, "sensor_fit", fit.fit)
    _set(cam, "sensor_width", fit.sensor_width)
    _set(cam, "sensor_height", fit.sensor_height)
    if st.overscan != overscan:
        st.overscan = overscan

    if path == "RAYTRACE" and effect:
        backend_osl.enable(cam)
    else:
        backend_osl.disable(cam)

    if is_scene_cam:
        backend_comp.sync(scene, cam_ob, state, path if effect else "NONE", overscan, rebake)
        apply_scene_settings(cam, scene)


def update_frame(cam_ob, scene, depsgraph=None):
    """Per-frame work. Only the post-process ST-map depends on animated values.

    During an animation render, Blender evaluates the animation on the render
    depsgraph's copies and does not flush it back to the original data
    (verified), so read the *evaluated* camera. The overscan is kept constant
    (see stable_overscan), so the camera itself never changes here.
    """
    global _BUSY
    cam = cam_ob.data
    if _BUSY or not is_active(cam) or scene.camera != cam_ob or backend(cam_ob, scene) != "COMPOSITOR":
        return False
    ev_ob = cam_ob.evaluated_get(depsgraph) if depsgraph is not None else cam_ob
    state = lens_state(ev_ob, scene)
    if not has_lens_effect(state):
        return False
    from . import backend_comp
    _BUSY = True
    try:
        return backend_comp.bake_stmap(scene, state, cam.lensder.overscan) is not None
    finally:
        _BUSY = False


def _lens_fcurves(cam):
    """F-curves of Lensder's animatable values on this camera: {name: fcurve}."""
    ad = cam.animation_data
    if ad is None or ad.action is None:
        return {}
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    except Exception:
        return {}
    if bag is None:
        return {}
    out = {}
    for fc in bag.fcurves:
        p = fc.data_path
        if p.startswith('["' + idprops.PREFIX) and p.endswith('"]'):
            out[p[2 + len(idprops.PREFIX):-2]] = fc
    return out


def stable_overscan(cam_ob, scene, state):
    """Overscan for the post-process path, constant for a given lens setup.

    The camera sensor must not change from frame to frame. Blender evaluates
    the render depsgraph before frame-change handlers run, so a per-frame
    sensor change would reach the render one frame late (verified). So take the
    worst case over the zoom range and over keyframed lens values. Reading
    F-curves is cheap; no scene re-evaluation is needed.
    """
    cam = cam_ob.data
    st = cam.lensder
    variants = [state]
    if state.params.get("breathing", 0.0) != 0.0:
        variants.append(L.LensState(state.model, state.focal, state.frame_w, state.frame_h, 0.0, state.params))
    curves = _lens_fcurves(cam)
    frames = []
    if curves:
        f0, f1 = scene.frame_start, scene.frame_end
        n = min(48, max(1, f1 - f0))
        frames = [f0 + (f1 - f0) * i / n for i in range(n + 1)]
        for fc in curves.values():
            frames += [k.co[0] for k in fc.keyframe_points]
    if st.mode == "PRESET":
        lens = lens_preset(st)
        focals = [lens.focal_min + (lens.focal_max - lens.focal_min) * i / 8 for i in range(9)] if lens.is_zoom else []
        amt = list(amounts(cam))
        for fr in frames:
            for i, (name, _l, _d) in enumerate(idprops.AMOUNTS):
                if name in curves:
                    amt[i] = max(amt[i], curves[name].evaluate(fr))
        for f in focals or [focal(cam)]:
            model, params = presets.compile_params(lens, f, fstop(cam), *amt)
            variants.append(L.LensState(model, f, state.frame_w, state.frame_h, 0.0, params))
    else:
        for fr in frames:
            params = dict(state.params)
            for name, fc in curves.items():
                if name in params:
                    params[name] = fc.evaluate(fr)
            f = curves["focal"].evaluate(fr) if "focal" in curves else state.focal
            variants.append(L.LensState(state.model, f, state.frame_w, state.frame_h, 0.0, params))
    need = max(S.required_overscan(v) for v in variants)
    return math.ceil(need * 1000.0) / 1000.0  # quantise: tiny changes must not re-fit the camera


def apply_scene_settings(cam, scene):
    """Exposure, shutter and white balance live on the scene; follow the active camera."""
    st = cam.lensder
    vs = scene.view_settings
    if st.use_exposure:
        ev = st.ev_comp
        if st.exposure_mode == "MANUAL":
            ev += E.relative_stops(fstop(cam), shutter_seconds(st, scene), st.iso,
                                   st.ref_fstop, 1.0 / st.ref_shutter_inv, st.ref_iso)
        _set(vs, "exposure", max(-32.0, min(32.0, ev)))
    if st.use_shutter:
        _set(scene.render, "use_motion_blur", True)
        _set(scene.render, "motion_blur_shutter", E.motion_blur_fraction(shutter_seconds(st, scene), fps(scene)))
    if st.use_white_balance:
        _set(vs, "use_white_balance", True)
        _set(vs, "white_balance_temperature", st.wb_temperature)
        _set(vs, "white_balance_tint", E.blender_tint(st.wb_tint))


def fps(scene):
    return scene.render.fps / scene.render.fps_base


def shutter_seconds(st, scene):
    if st.shutter_mode == "ANGLE":
        return E.shutter_seconds_from_angle(st.shutter_angle, fps(scene))
    return 1.0 / st.shutter_inv


# ---------------------------------------------------------------------------
# Property update entry points
# ---------------------------------------------------------------------------

def _owner_objects(cam):
    return [ob for ob in bpy.data.objects if ob.type == "CAMERA" and ob.data == cam]


def _context_scene(context):
    return getattr(context, "scene", None) or bpy.context.scene


def refresh_camera_data(cam, context=None):
    scene = _context_scene(context) if context else bpy.context.scene
    obs = _owner_objects(cam)
    # Prefer the scene camera so scene-level settings follow it.
    obs.sort(key=lambda ob: ob != scene.camera)
    for ob in obs:
        apply(ob, scene)
    from . import overlay
    overlay.invalidate()


def on_settings_changed(settings, context):
    refresh_camera_data(settings.id_data, context)


def on_preset_changed(settings, context):
    cam = settings.id_data
    lens = lens_preset(settings)
    idprops.ensure(cam)
    # A prime has one focal length; a zoom keeps the current one if it is in range.
    f = idprops.get(cam, "focal")
    idprops.set(cam, "focal", min(max(f, lens.focal_min), lens.focal_max))
    idprops.set(cam, "fstop", max(idprops.get(cam, "fstop"), lens.max_aperture))
    apply_lens_limits(cam)
    cam.dof.aperture_blades = lens.blades
    cam.dof.aperture_ratio = lens.squeeze  # anamorphic: vertically stretched oval bokeh
    settings.squeeze = lens.squeeze
    # Post effects (chromatic aberration, flares) stay opt-in: they add a node to
    # the user's compositor. The UI shows what this lens typically has.
    refresh_camera_data(cam, context)


def apply_lens_limits(cam):
    """A real lens cannot zoom past its range or open wider than its maximum aperture."""
    if cam.lensder.mode == "PRESET":
        lens = lens_preset(cam.lensder)
        idprops.set_range(cam, "focal", lens.focal_min, lens.focal_max)
        idprops.set_range(cam, "fstop", lens.max_aperture, 32.0)
    else:
        idprops.set_range(cam, "focal", 1.0, 5000.0)
        idprops.set_range(cam, "fstop", 0.5, 128.0)


def on_mode_changed(settings, context):
    cam = settings.id_data
    previous = settings.last_mode
    settings.last_mode = settings.mode
    if settings.mode != "NATIVE":
        idprops.ensure(cam)
    if settings.mode == "ADVANCED" and previous == "PRESET":
        # Start Advanced mode from the current look (the preset's effective values).
        model, params = presets.compile_params(lens_preset(settings), focal(cam), fstop(cam), *amounts(cam))
        _copy_params_to_advanced(cam, model, params)
    elif settings.mode == "PRESET" and not settings.configured:
        guess_body_and_lens(cam, settings)
    if settings.mode != "NATIVE":
        settings.configured = True
        apply_lens_limits(cam)
    refresh_camera_data(cam, context)


def _copy_params_to_advanced(cam, model, params):
    settings = cam.lensder
    settings.model = model
    for p in idprops.FLOAT_PARAMS:
        idprops.set(cam, p.name, params[p.name])
    settings.fish_proj = schema.FISHEYE_PROJECTIONS[int(params["fish_proj"])][0]
    settings.squeeze = lens_preset(settings).squeeze


def guess_body_and_lens(cam, settings):
    """First switch into Camera Kit mode: pick the body and lens closest to the current camera."""
    width = cam.sensor_width
    best = min(presets.BODIES, key=lambda b: abs(b.width - width))
    if abs(best.width - width) < 0.5:
        settings.body = best.id
    else:
        settings.body = "CUSTOM"
        settings.sensor_w = width
        settings.sensor_h = cam.sensor_height
    spherical = [lens for lens in presets.LENSES if lens.kind == "SPHERICAL" and lens.id != "PHONE"]
    primes = [lens for lens in spherical if not lens.is_zoom]
    f = focal(cam)
    nearest_prime = min(primes, key=lambda lens: abs(lens.focal_min - f))
    containing = [lens for lens in spherical if lens.is_zoom and lens.focal_min <= f <= lens.focal_max]
    if abs(nearest_prime.focal_min - f) <= 1.0 or not containing:
        choice = nearest_prime
    else:
        choice = containing[0]
    if settings.lens_preset != choice.id:
        settings.lens_preset = choice.id  # triggers on_preset_changed
    else:
        on_preset_changed(settings, bpy.context)


def on_scene_toggle(settings, context, which):
    """When a scene-level control is switched on, start from the scene's current value,
    so enabling it changes nothing visible."""
    scene = _context_scene(context)
    cam = settings.id_data
    vs = scene.view_settings
    if which == "exposure" and settings.use_exposure:
        settings.ev_comp = vs.exposure
        settings.ref_fstop = fstop(cam)
        settings.ref_iso = settings.iso
        settings.ref_shutter_inv = 1.0 / shutter_seconds(settings, scene)
    elif which == "shutter" and settings.use_shutter:
        frac = scene.render.motion_blur_shutter
        settings.shutter_angle = frac * 360.0
        settings.shutter_inv = fps(scene) / max(frac, 1e-6)
    elif which == "white_balance" and settings.use_white_balance:
        if vs.use_white_balance:
            settings.wb_temperature = vs.white_balance_temperature
            settings.wb_tint = vs.white_balance_tint - E.BLENDER_NEUTRAL_TINT
    refresh_camera_data(cam, context)


# ---------------------------------------------------------------------------
# Read-outs for the UI
# ---------------------------------------------------------------------------

def readouts(cam_ob, scene):
    cam = cam_ob.data
    st = cam.lensder
    fit = gate_fit(cam, scene)
    state = lens_state(cam_ob, scene)
    out = {
        "frame": (fit.frame_w, fit.frame_h),
        "crop": optics.crop_factor(fit.frame_w, fit.frame_h),
        "hfov": optics.fov_deg(fit.frame_w, state.focal_eff),
        "vfov": optics.fov_deg(fit.frame_h, state.focal_eff),
        "distortion": L.corner_distortion_percent(state),
        "overscan": st.overscan,
        "backend": backend(cam_ob, scene),
        "focal_eff": state.focal_eff,
    }
    d = focus_distance(cam_ob)
    if cam.dof.use_dof and d > 0:
        coc = optics.circle_of_confusion(fit.frame_w / max(squeeze(st), 1e-6), fit.frame_h)
        out["dof"] = optics.depth_of_field(focal(cam), fstop(cam), d, coc)
    if st.use_exposure or st.exposure_mode == "MANUAL":
        out["ev100"] = E.ev100(fstop(cam), shutter_seconds(st, scene), st.iso)
    if st.use_shutter:
        sec = shutter_seconds(st, scene)
        out["shutter"] = (E.format_shutter(sec), E.shutter_angle_from_seconds(sec, fps(scene)))
    if state.model == "FISHEYE":
        out["distortion"] = None
    return out


def _has_fcurve(cam, data_path):
    try:
        from bpy_extras import anim_utils
        ad = cam.animation_data
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        return bag is not None and bag.fcurves.find(data_path) is not None
    except Exception:
        return False


def zoom_range(cam):
    st = cam.lensder
    if st.mode != "PRESET":
        return None
    lens = lens_preset(st)
    return lens.focal_min, lens.focal_max


def warnings(cam_ob, scene):
    cam = cam_ob.data
    st = cam.lensder
    out = []
    if st.mode == "NATIVE":
        return out
    if scene.camera != cam_ob:
        out.append("Not the scene camera: exposure, white balance and post effects follow the active camera")
    if st.mode == "PRESET":
        lens = lens_preset(st)
        f, n = focal(cam), fstop(cam)
        if f < lens.focal_min - 0.01 or f > lens.focal_max + 0.01:
            rng = f"{lens.focal_min:g} mm" if not lens.is_zoom else f"{lens.focal_min:g}–{lens.focal_max:g} mm"
            out.append(f"Focal length outside this lens' range ({rng})")
        if n < lens.max_aperture - 1e-3:
            out.append(f"f/{n:g} is wider than this lens can open (f/{lens.max_aperture:g})")
    if is_active(cam) and cam.animation_data and cam.animation_data.action and idprops.is_linked(cam):
        if _has_fcurve(cam, "lens") or _has_fcurve(cam, "dof.aperture_fstop"):
            out.append("Blender's own focal/f-stop keyframes are overridden; animate Lensder's instead")
    path = backend(cam_ob, scene)
    if st.method == "RAYTRACE":
        ok, why = raytrace_supported(scene)
        if not ok:
            out.append(why)
    state = lens_state(cam_ob, scene)
    if path == "COMPOSITOR" and state.model == "FISHEYE":
        out.append("Fisheye in post-process loses resolution at the edges; prefer Ray-Traced (Cycles)")
    if path == "COMPOSITOR" and st.overscan >= S.MAX_OVERSCAN - 1e-6:
        out.append("Field of view too wide for post-process; edges are cropped")
    if path == "RAYTRACE" and state.model != "NONE" and not math.isclose(cam.shift_x + cam.shift_y, 0.0):
        out.append("Lens shift with a ray-traced lens is applied after distortion")
    return out
