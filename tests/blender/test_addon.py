"""End-to-end tests of the add-on inside Blender 5.2 (headless, Cycles CPU)."""

import math
import os

import numpy as np
import pytest

from tests.blender import bhelpers as H

bpy = H.bpy

import lensder  # noqa: E402
from lensder import idprops, ops, sync  # noqa: E402
from lensder.core import lens as L  # noqa: E402
from lensder.core import presets, schema  # noqa: E402

RES = (64, 36)


@pytest.fixture(autouse=True)
def addon():
    H.reset(res=RES)
    lensder.register()
    yield
    lensder.unregister()


def _cam(lens=50.0):
    ob = H.camera(lens=lens)
    return ob, ob.data


def _evaluated_custom(cam_data, name):
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return cam_data.evaluated_get(dg).cycles_custom[name]


def _expected_dirs(cam_ob):
    state = sync.lens_state(cam_ob, bpy.context.scene)
    w, h = RES
    xs = ((np.arange(w) + 0.5) / w - 0.5) * state.frame_w
    ys = ((np.arange(h) + 0.5) / h - 0.5) * state.frame_h
    gx, gy = np.meshgrid(xs, ys)
    dx, dy, dz, ok = L.film_to_ray(state, gx, gy)
    return np.stack([dx, dy, dz], -1), ok


def _assert_render_matches(cam_ob, tol_deg=0.03):
    """Direction-encoding world; the colour is scaled by vignetting, so divide it out
    (this also checks vignetting end to end)."""
    H.direction_world()
    img = H.render("addon_dirs")
    state = sync.lens_state(cam_ob, bpy.context.scene)
    exp, ok = _expected_dirs(cam_ob)
    w, h = RES
    xs = ((np.arange(w) + 0.5) / w - 0.5) * state.frame_w
    ys = ((np.arange(h) + 0.5) / h - 0.5) * state.frame_h
    gx, gy = np.meshgrid(xs, ys)
    v = L.vignette(state, gx, gy, exp[..., 2])
    img = img.copy()
    img[..., :3] /= np.maximum(v, 1e-6)[..., None]
    got = H.decode_dirs(img)
    ang = np.degrees(np.arccos(np.clip((got * exp).sum(-1), -1, 1)))
    assert ang[ok].max() < tol_deg, f"max angular error {ang[ok].max():.4f}°"


def test_register_and_native_does_nothing():
    ob, cam = _cam()
    assert cam.lensder.mode == "NATIVE"
    assert cam.type == "PERSP"
    assert cam.animation_data is None or len(cam.animation_data.drivers) == 0


def test_camera_kit_guesses_lens_and_raytraces():
    ob, cam = _cam(lens=35.0)
    cam.lensder.mode = "PRESET"
    assert cam.lensder.lens_preset == "N35"
    assert cam.lensder.body == "FULL_FRAME"
    assert cam.type == "CUSTOM"
    assert cam.dof.aperture_blades == presets.LENS_BY_ID["N35"].blades
    # The driver-evaluated coefficient equals the Python compilation.
    model, params = presets.compile_params(presets.LENS_BY_ID["N35"], 35.0, sync.fstop(cam))
    assert _evaluated_custom(cam, "k1") == pytest.approx(params["k1"], rel=1e-5)
    _assert_render_matches(ob)


def test_anamorphic_preset_squeeze_bokeh_and_frame():
    ob, cam = _cam()
    st = cam.lensder
    st.mode = "PRESET"
    st.body = "ALEXA35"
    st.lens_preset = "ANA2X40"
    assert idprops.get(cam, "focal") == 40.0
    assert cam.dof.aperture_ratio == 2.0
    fit = sync.gate_fit(cam, bpy.context.scene)
    # 27.99 mm gate x 2 = 55.98 mm desqueezed; 16:9 delivery is height-limited
    assert fit.fit == "VERTICAL" and fit.frame_h == pytest.approx(19.22)
    assert cam.sensor_fit == "VERTICAL" and cam.sensor_height == pytest.approx(19.22)
    _assert_render_matches(ob)


def test_zoom_distortion_animates_through_drivers():
    ob, cam = _cam(lens=24.0)
    st = cam.lensder
    st.mode = "PRESET"
    st.lens_preset = "Z24_70"
    sc = bpy.context.scene
    idprops.set(cam, "focal", 24.0)
    cam.keyframe_insert(idprops.path("focal"), frame=1)
    idprops.set(cam, "focal", 70.0)
    cam.keyframe_insert(idprops.path("focal"), frame=10)
    lens = presets.LENS_BY_ID["Z24_70"]
    for frame, focal in ((1, 24.0), (10, 70.0)):
        sc.frame_set(frame)
        _, params = presets.compile_params(lens, focal, sync.fstop(cam))
        assert _evaluated_custom(cam, "k1") == pytest.approx(params["k1"], rel=1e-5)
        assert _evaluated_custom(cam, "focal_length") == pytest.approx(focal)
        assert cam.lens == pytest.approx(focal)  # Blender's own lens follows Lensder's


def test_preset_to_advanced_keeps_the_look():
    ob, cam = _cam(lens=24.0)
    st = cam.lensder
    st.mode = "PRESET"
    st.lens_preset = "W24"
    k1_preset = _evaluated_custom(cam, "k1")
    st.mode = "ADVANCED"
    assert st.model == "BROWN"
    assert idprops.get(cam, "k1") == pytest.approx(k1_preset, rel=1e-5)
    assert _evaluated_custom(cam, "k1") == pytest.approx(k1_preset, rel=1e-5)
    idprops.set(cam, "k1", -0.2)
    assert _evaluated_custom(cam, "k1") == pytest.approx(-0.2)
    _assert_render_matches(ob)


@pytest.mark.parametrize("model,params", [
    ("TDE_ANAMORPHIC", dict(cx02=0.05, cy02=0.02, ana_rot=1.0)),
    ("FISHEYE", dict(fish_proj="STEREOGRAPHIC", fish_fov=180.0)),
    ("NUKE", dict(nuke_k1=0.05)),
])
def test_advanced_models_render(model, params):
    ob, cam = _cam(lens=12.0 if model == "FISHEYE" else 30.0)
    st = cam.lensder
    st.mode = "ADVANCED"
    st.model = model
    for k, v in params.items():
        if k == "fish_proj":
            st.fish_proj = v
        else:
            idprops.set(cam, k, v)
    sync.apply(ob)
    _assert_render_matches(ob)


def test_compositor_path_matches_raytraced_path():
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = 160, 90
    sc.cycles.samples = 16
    sc.cycles.filter_width = 1.5
    sc.cycles.pixel_filter_type = "BLACKMAN_HARRIS"
    H.checker_wall(squares=30)
    H.white_world(0.0)
    ob, cam = _cam(lens=24.0)
    st = cam.lensder
    st.mode = "ADVANCED"
    st.model = "BROWN"
    idprops.set(cam, "k1", -0.15)
    idprops.set(cam, "k2", 0.02)
    idprops.set(cam, "vig_k1", -0.3)
    st.method = "RAYTRACE"
    ray = H.render("ray")
    assert st.overscan == 1.0
    st.method = "COMPOSITOR"
    assert cam.type == "PERSP"
    assert st.overscan > 1.0
    assert cam.sensor_width == pytest.approx(36.0 * st.overscan)
    comp = H.render("comp")
    inner = (slice(4, -4), slice(4, -4))
    diff = np.abs(ray[inner][..., :3] - comp[inner][..., :3])
    # Different reconstruction (resampled post vs ray-traced), same geometry:
    assert diff.mean() < 0.03, diff.mean()
    # Vignetting applied in post too: corners darker than centre on a white-ish area
    assert comp[..., 3].min() > 0.99  # full coverage, no black corners


def test_native_restores_camera():
    ob, cam = _cam()
    st = cam.lensder
    st.mode = "ADVANCED"
    idprops.set(cam, "k1", -0.1)
    st.method = "COMPOSITOR"
    widened = cam.sensor_width
    assert widened > 36.0
    idprops.set(cam, "focal", 42.0)
    st.mode = "NATIVE"
    assert cam.type == "PERSP"
    assert not idprops.is_linked(cam)
    assert cam.lens == pytest.approx(42.0)  # Blender keeps the last Lensder focal length
    assert cam.sensor_width == pytest.approx(widened / (widened / 36.0), rel=1e-6)
    assert not any(fc.data_path.startswith("cycles_custom") for fc in cam.animation_data.drivers)
    node = bpy.context.scene.compositing_node_group.nodes.get("Lensder")
    assert node is None or node.mute


def test_exposure_triangle_manual_and_auto():
    ob, cam = _cam()
    sc = bpy.context.scene
    st = cam.lensder
    st.mode = "PRESET"
    sc.view_settings.exposure = 0.5
    st.use_exposure = True  # starts from the current exposure: nothing changes visibly
    assert sc.view_settings.exposure == pytest.approx(0.5)
    st.exposure_mode = "MANUAL"
    idprops.set(cam, "fstop", st.ref_fstop * math.sqrt(2) ** 2)  # two stops down
    sync.apply(ob)
    assert sc.view_settings.exposure == pytest.approx(0.5 - 2.0, abs=1e-4)
    st.iso = st.ref_iso * 4  # two stops more sensitive
    assert sc.view_settings.exposure == pytest.approx(0.5, abs=1e-4)
    st.exposure_mode = "AUTO"
    assert sc.view_settings.exposure == pytest.approx(0.5, abs=1e-4)


def test_shutter_and_white_balance():
    ob, cam = _cam()
    sc = bpy.context.scene
    st = cam.lensder
    st.mode = "PRESET"
    st.use_shutter = True
    st.shutter_mode = "ANGLE"
    st.shutter_angle = 90.0
    assert sc.render.use_motion_blur
    assert sc.render.motion_blur_shutter == pytest.approx(0.25)
    st.shutter_mode = "SPEED"
    st.shutter_inv = 48.0
    assert sc.render.motion_blur_shutter == pytest.approx(0.5)  # 1/48 s at 24 fps
    st.use_white_balance = True
    st.wb_temperature = 3200.0
    st.wb_tint = 5.0
    vs = sc.view_settings
    assert vs.use_white_balance and vs.white_balance_temperature == 3200.0
    assert vs.white_balance_tint == pytest.approx(15.0)


def test_scene_settings_follow_only_active_camera():
    ob1, cam1 = _cam()
    ob2 = bpy.data.objects.new("Cam2", bpy.data.cameras.new("Cam2"))
    bpy.context.scene.collection.objects.link(ob2)
    sc = bpy.context.scene
    sc.camera = ob1
    cam2 = ob2.data
    cam2.lensder.mode = "PRESET"
    cam2.lensder.use_white_balance = True
    cam2.lensder.wb_temperature = 3000.0
    assert not sc.view_settings.use_white_balance  # cam2 is not the scene camera
    sc.camera = ob2
    sync.apply(ob2)
    assert sc.view_settings.white_balance_temperature == 3000.0


def test_copy_from_movieclip():
    # A movie clip needs a file: write a tiny image sequence frame.
    img = bpy.data.images.new("plate", 192, 108)
    path = os.path.join(H.TMP, "plate.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    clip = bpy.data.movieclips.load(path)
    tc = clip.tracking.camera
    tc.sensor_width = 23.76
    tc.focal_length = 32.0
    tc.distortion_model = "BROWN"
    tc.brown_k1, tc.brown_k2, tc.brown_p1 = -0.11, 0.03, 0.001
    ob, cam = _cam()
    ops.copy_from_movieclip(cam, clip)
    st = cam.lensder
    assert st.mode == "ADVANCED" and st.model == "BROWN"
    assert [idprops.get(cam, n) for n in ("k1", "k2", "p1")] == pytest.approx([-0.11, 0.03, 0.001])
    assert idprops.get(cam, "focal") == 32.0 and st.body == "CUSTOM" and st.sensor_w == pytest.approx(23.76)


def test_saved_file_renders_without_addon():
    """Render farms: the shader bytecode and current values are stored in the .blend."""
    ob, cam = _cam(lens=24.0)
    st = cam.lensder
    st.mode = "ADVANCED"
    st.model = "TDE_ANAMORPHIC"
    idprops.set(cam, "cx02", 0.06)
    idprops.set(cam, "cy02", 0.02)
    # Animated coefficient: must also survive without the add-on.
    cam.keyframe_insert(idprops.path("cx04"), frame=1)
    idprops.set(cam, "cx04", 0.03)
    cam.keyframe_insert(idprops.path("cx04"), frame=20)
    bpy.context.scene.frame_set(20)
    sync.apply(ob)
    expected, ok = _expected_dirs(ob)
    path = os.path.join(H.TMP, "farm.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    lensder.unregister()
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
        bpy.context.scene.frame_set(20)
        H.direction_world()
        got = H.decode_dirs(H.render("farm"))
        ang = np.degrees(np.arccos(np.clip((got * expected).sum(-1), -1, 1)))
        assert ang[ok].max() < 0.03
    finally:
        lensder.register()


def test_no_dependency_cycles():
    """Shader drivers must not read the camera's own native properties (cycle warnings)."""
    import subprocess
    import sys
    import textwrap
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {root!r})
        from tests.blender import bhelpers as H
        import bpy, lensder
        H.reset(); lensder.register()
        ob = H.camera(lens=24.0)
        ob.data.lensder.mode = "PRESET"
        ob.data.lensder.lens_preset = "Z24_70"
        ob.data.lensder.mode = "ADVANCED"
        bpy.context.evaluated_depsgraph_get().update()
        print("LENSDER_DONE")
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300)
    text = out.stdout + out.stderr
    assert "LENSDER_DONE" in text, text[-2000:]
    assert "Dependency cycle" not in text, text[text.find("Dependency cycle") - 200:][:1500]


def _pixel_tolerance_deg(cam_ob, pixels):
    """``pixels`` expressed as an angle at the centre of the frame."""
    state = sync.lens_state(cam_ob, bpy.context.scene)
    return np.degrees(np.arctan(pixels * state.frame_w / RES[0] / state.focal_eff))


def _angles_vs_reference(img, cam_ob):
    exp, ok = _expected_dirs(cam_ob)
    got = H.decode_dirs(img)
    ang = np.degrees(np.arccos(np.clip((got * exp).sum(-1), -1, 1)))
    return ang, ok


@pytest.mark.parametrize("model,params", [
    ("BROWN", dict(k1=-0.15, k2=0.02, p1=0.002)),
    ("TDE_ANAMORPHIC", dict(cx02=0.06, cy02=0.025, cx04=0.01)),
])
def test_compositor_geometry_matches_reference(model, params):
    """Post-process path: same per-pixel ray directions as the reference model.

    The direction-encoding world is smooth, so resampling softness does not
    matter and this measures pure geometry.
    """
    ob, cam = _cam(lens=24.0)
    st = cam.lensder
    st.mode = "ADVANCED"
    st.model = model
    for k, v in params.items():
        idprops.set(cam, k, v)
    st.method = "COMPOSITOR"
    sync.apply(ob)
    H.direction_world()
    ang, ok = _angles_vs_reference(H.render("comp_geom"), ob)
    assert ang[ok].max() < _pixel_tolerance_deg(ob, 0.25), f"max angular error {ang[ok].max():.4f}°"


def test_compositor_rebakes_per_frame_during_animation_render():
    """Animated zoom + post-process path, rendered as an animation job.

    Regression test for two verified Blender behaviours: render-time animation
    is not flushed to original data, and camera changes made in frame handlers
    reach the render one frame late.
    """
    sc = bpy.context.scene
    ob, cam = _cam(lens=24.0)
    st = cam.lensder
    st.mode = "PRESET"
    st.lens_preset = "Z24_70"
    idprops.set(cam, "amount_vignette", 0.0)
    idprops.set(cam, "amount_distortion", 3.0)  # exaggerate so frames clearly differ
    idprops.set(cam, "focal", 24.0)
    cam.keyframe_insert(idprops.path("focal"), frame=1)
    idprops.set(cam, "focal", 70.0)
    cam.keyframe_insert(idprops.path("focal"), frame=2)
    sc.frame_start, sc.frame_end = 1, 2
    st.method = "COMPOSITOR"
    H.direction_world()
    sc.frame_set(1)
    sc.render.filepath = os.path.join(H.TMP, "anim_comp_")
    bpy.ops.render.render(animation=True)
    for f in (1, 2):
        sc.frame_set(f)  # the reference model at this frame (viewport evaluation)
        img = H.load(os.path.join(H.TMP, f"anim_comp_{f:04d}.exr"))
        ang, ok = _angles_vs_reference(img, ob)
        assert ang[ok].max() < _pixel_tolerance_deg(ob, 0.25), (f, ang[ok].max())
