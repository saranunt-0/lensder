"""Operators."""

import bpy
from bpy.props import StringProperty

from . import backend_comp, idprops, sync
from .core import optics
from .core import stmap as S


def _camera_object(context):
    ob = context.active_object
    if ob is not None and ob.type == "CAMERA":
        return ob
    return context.scene.camera


class LENSDER_OT_meter(bpy.types.Operator):
    """Use the current aperture, shutter and ISO as the reference for correct exposure"""

    bl_idname = "lensder.meter"
    bl_label = "Meter Here"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ob = _camera_object(context)
        return ob is not None and sync.is_active(ob.data)

    def execute(self, context):
        ob = _camera_object(context)
        st = ob.data.lensder
        st.ref_fstop = sync.fstop(ob.data)
        st.ref_iso = st.iso
        st.ref_shutter_inv = 1.0 / sync.shutter_seconds(st, context.scene)
        self.report({"INFO"}, "Exposure reference set; brightness now changes relative to these settings")
        return {"FINISHED"}


class LENSDER_OT_make_active(bpy.types.Operator):
    """Make this the scene's active camera"""

    bl_idname = "lensder.make_active"
    bl_label = "Make Active Camera"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ob = context.active_object
        if ob is None or ob.type != "CAMERA":
            return {"CANCELLED"}
        context.scene.camera = ob
        sync.apply(ob, context.scene)
        return {"FINISHED"}


class LENSDER_OT_viewport_preview(bpy.types.Operator):
    """Look through the camera with a preview that shows the lens (Rendered for Cycles ray-traced,
    viewport compositor for post-process)"""

    bl_idname = "lensder.viewport_preview"
    bl_label = "Preview Lens in Viewport"

    def execute(self, context):
        ob = _camera_object(context)
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None) if context.screen else None
        if ob is None or area is None:
            self.report({"WARNING"}, "No 3D Viewport found")
            return {"CANCELLED"}
        context.scene.camera = ob
        space = area.spaces.active
        space.region_3d.view_perspective = "CAMERA"
        path = sync.backend(ob, context.scene)
        if path == "RAYTRACE":
            space.shading.type = "RENDERED"
        elif path == "COMPOSITOR":
            if space.shading.type == "WIREFRAME":
                space.shading.type = "SOLID"
            space.shading.use_compositor = "CAMERA"
        return {"FINISHED"}


class LENSDER_OT_refresh(bpy.types.Operator):
    """Re-apply Lensder to this camera (recompile shader, rebuild drivers, re-bake ST-map)"""

    bl_idname = "lensder.refresh"
    bl_label = "Refresh Lens"

    def execute(self, context):
        ob = _camera_object(context)
        if ob is None:
            return {"CANCELLED"}
        st = ob.data.lensder
        st.shader_hash = ""
        st.driver_signature = ""
        sync.apply(ob, context.scene)
        return {"FINISHED"}


class LENSDER_OT_from_movieclip(bpy.types.Operator):
    """Copy the lens solved in Blender's Motion Tracker (Movie Clip camera) into Advanced mode,
    so CG rendered through this camera matches the footage"""

    bl_idname = "lensder.from_movieclip"
    bl_label = "Copy Lens from Movie Clip"
    bl_options = {"REGISTER", "UNDO"}

    clip: StringProperty(name="Movie Clip")

    def invoke(self, context, event):
        if not bpy.data.movieclips:
            self.report({"WARNING"}, "No movie clips in this file")
            return {"CANCELLED"}
        if not self.clip:
            space_clip = context.space_data.clip if context.space_data and context.space_data.type == "CLIP_EDITOR" else None
            self.clip = (space_clip or context.scene.active_clip or bpy.data.movieclips[0]).name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, "clip", bpy.data, "movieclips")

    def execute(self, context):
        ob = _camera_object(context)
        clip = bpy.data.movieclips.get(self.clip)
        if ob is None or clip is None:
            return {"CANCELLED"}
        copy_from_movieclip(ob.data, clip)
        sync.apply(ob, context.scene)
        self.report({"INFO"}, f"Lens copied from '{clip.name}'")
        return {"FINISHED"}


def copy_from_movieclip(cam, clip):
    tc = clip.tracking.camera
    st = cam.lensder
    w, h = clip.size
    pa = tc.pixel_aspect
    st.mode = "ADVANCED"
    idprops.ensure(cam)
    st.body = "CUSTOM"
    st.sensor_w = tc.sensor_width
    st.sensor_h = tc.sensor_width * h / max(w, 1)
    st.squeeze = pa
    idprops.set(cam, "focal", tc.focal_length)
    values = dict.fromkeys(("k1", "k2", "k3", "k4", "p1", "p2", "div_k1", "div_k2",
                            "nuke_k1", "nuke_k2", "nuke_p1", "nuke_p2"), 0.0)
    model = tc.distortion_model
    if model == "POLYNOMIAL":
        st.model = "BROWN"
        values.update(k1=tc.k1, k2=tc.k2, k3=tc.k3)
    elif model == "BROWN":
        st.model = "BROWN"
        values.update(k1=tc.brown_k1, k2=tc.brown_k2, k3=tc.brown_k3, k4=tc.brown_k4,
                      p1=tc.brown_p1, p2=tc.brown_p2)
    elif model == "DIVISION":
        st.model = "DIVISION"
        values.update(div_k1=tc.division_k1, div_k2=tc.division_k2)
    elif model == "NUKE":
        st.model = "NUKE"
        values.update(nuke_k1=tc.nuke_k1, nuke_k2=tc.nuke_k2, nuke_p1=tc.nuke_p1, nuke_p2=tc.nuke_p2)
    # principal_point is normalised to [-1, 1] across the frame.
    values["center_x"] = tc.principal_point[0] * st.sensor_w * pa * 0.5
    values["center_y"] = tc.principal_point[1] * st.sensor_h * 0.5
    for name, value in values.items():
        idprops.set(cam, name, value)


class LENSDER_OT_export_stmap(bpy.types.Operator):
    """Save the distortion ST-map as an OpenEXR (for Nuke, Fusion, After Effects...). Red/green: where to sample
    an undistorted (overscanned) render, blue: coverage, alpha: vignetting"""

    bl_idname = "lensder.export_stmap"
    bl_label = "Export ST-Map"

    filepath: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.abspath("//lensder_stmap.exr") if bpy.data.filepath else "lensder_stmap.exr"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ob = _camera_object(context)
        if ob is None or not sync.is_active(ob.data):
            return {"CANCELLED"}
        scene = context.scene
        state = sync.lens_state(ob, scene)
        over = S.required_overscan(state)
        img = backend_comp.bake_stmap(scene, state, over, force=True)
        img.filepath_raw = bpy.path.abspath(self.filepath)
        img.file_format = "OPEN_EXR"
        img.save()
        fit = optics.fit_gate(*sync.sensor_size(ob.data.lensder), sync.squeeze(ob.data.lensder),
                              sync.render_aspect(scene), over)
        self.report({"INFO"}, f"Saved {self.filepath}; render the plate with overscan {over:.4f} "
                              f"(sensor {fit.sensor_width:.3f} mm)")
        return {"FINISHED"}


CLASSES = (
    LENSDER_OT_meter,
    LENSDER_OT_make_active,
    LENSDER_OT_viewport_preview,
    LENSDER_OT_refresh,
    LENSDER_OT_from_movieclip,
    LENSDER_OT_export_stmap,
)
