"""Viewport overlay: distorted grid and true frame in camera view (any shading mode)."""

import bpy

from . import sync
from .core import overlay_geom

_handle = None
_cache = {"key": None, "lines": [], "frame": []}


def invalidate():
    _cache["key"] = None


def _geometry(cam_ob, scene):
    cam = cam_ob.data
    st = cam.lensder
    state = sync.lens_state(cam_ob, scene)
    key = (cam_ob.name, state.model, tuple(sorted(state.params.items())), round(state.frame_w, 5),
           round(state.frame_h, 5), round(state.focal, 5), round(state.focus_distance, 4), st.overlay_density)
    if _cache["key"] != key:
        _cache["key"] = key
        _cache["lines"] = overlay_geom.grid_polylines(state, st.overlay_density)
        _cache["frame"] = overlay_geom.frame_outline(state)
    return state


def _segments(polylines, to_px):
    pts = []
    for line in polylines:
        for a, b in zip(line[:-1], line[1:]):
            pts.append(to_px(a))
            pts.append(to_px(b))
    return pts


def _draw():
    ctx = bpy.context
    rv3d = getattr(ctx, "region_data", None)
    scene = ctx.scene
    cam_ob = scene.camera if scene else None
    if rv3d is None or rv3d.view_perspective != "CAMERA" or cam_ob is None or cam_ob.type != "CAMERA":
        return
    cam = cam_ob.data
    if not sync.is_active(cam) or not cam.lensder.show_overlay:
        return
    try:
        import gpu
        from bpy_extras import view3d_utils
        from gpu_extras.batch import batch_for_shader
    except ImportError:
        return

    region = ctx.region
    corners = [cam_ob.matrix_world @ v for v in cam.view_frame(scene=scene)]
    pix = [view3d_utils.location_3d_to_region_2d(region, rv3d, c) for c in corners]
    if any(p is None for p in pix):
        return
    xs = [p.x for p in pix]
    ys = [p.y for p in pix]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    state = _geometry(cam_ob, scene)
    over = cam.lensder.overscan
    # The Blender camera frame spans the (possibly overscanned) film.
    full_w, full_h = state.frame_w * over, state.frame_h * over
    cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    sx, sy = (x1 - x0) / full_w, (y1 - y0) / full_h

    def to_px(p):
        return (cx + p[0] * sx, cy + p[1] * sy)

    try:
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        polyline = True
    except (ValueError, SystemError):
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        polyline = False
    gpu.state.blend_set("ALPHA")
    for polys, color, width in ((_cache["lines"], (1.0, 0.75, 0.2, 0.55), 1.0),
                                (_cache["frame"], (1.0, 1.0, 1.0, 0.9), 2.0)):
        pts = _segments(polys, to_px)
        if not pts:
            continue
        batch = batch_for_shader(shader, "LINES", {"pos": pts})
        shader.bind()
        shader.uniform_float("color", color)
        if polyline:
            shader.uniform_float("lineWidth", width)
            shader.uniform_float("viewportSize", (region.width, region.height))
        batch.draw(shader)
    gpu.state.blend_set("NONE")


def register():
    global _handle
    if _handle is None and not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
