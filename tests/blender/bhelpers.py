"""Helpers for integration tests that run inside Blender (``bpy`` module or blender -b).

Run with:  python -m pytest tests/blender   (with ``bpy`` importable)
"""

import glob
import os
import sys
import tempfile

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

# The pip ``bpy`` wheel keeps Blender's bundled site-packages (oslquery, needed
# to compile OSL cameras) off sys.path; a regular Blender install has them.
for _p in glob.glob(os.path.join(bpy.utils.resource_path("LOCAL"), "python", "lib", "python3*", "site-packages")):
    if _p not in sys.path:
        sys.path.append(_p)

import addon_utils  # noqa: E402

TMP = tempfile.mkdtemp(prefix="lensder_test_")


def reset(res=(64, 36), samples=1):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("cycles", default_set=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = False
    sc.cycles.use_adaptive_sampling = False
    sc.cycles.pixel_filter_type = "BOX"
    sc.cycles.filter_width = 0.01
    sc.render.compositor_device = "CPU"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "Standard"
    sc.render.image_settings.file_format = "OPEN_EXR"
    sc.render.image_settings.color_depth = "32"
    return sc


def direction_world():
    """World whose colour is the camera-space ray direction encoded as dir*0.5+0.5.

    The camera sits at the origin with identity rotation, so camera space is
    (x, y, -z) of world space.
    """
    w = bpy.data.worlds.new("dirs")
    bpy.context.scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    neg = nt.nodes.new("ShaderNodeMath")
    neg.operation = "MULTIPLY"
    neg.inputs[1].default_value = -1.0
    ma = nt.nodes.new("ShaderNodeVectorMath")
    ma.operation = "MULTIPLY_ADD"
    ma.inputs[1].default_value = (0.5, 0.5, 0.5)
    ma.inputs[2].default_value = (0.5, 0.5, 0.5)
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["X"], comb.inputs["X"])
    nt.links.new(sep.outputs["Y"], comb.inputs["Y"])
    nt.links.new(sep.outputs["Z"], neg.inputs[0])
    nt.links.new(neg.outputs[0], comb.inputs["Z"])
    nt.links.new(comb.outputs[0], ma.inputs[0])
    nt.links.new(ma.outputs[0], bg.inputs["Color"])
    nt.links.new(bg.outputs[0], out.inputs[0])
    return w


def white_world(strength=1.0):
    w = bpy.data.worlds.new("white")
    bpy.context.scene.world = w
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = strength
    return w


def checker_wall(distance=5.0, size=40.0, squares=40):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, -distance))
    ob = bpy.context.active_object
    mat = bpy.data.materials.new("checker")
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    ck = nt.nodes.new("ShaderNodeTexChecker")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    ck.inputs["Scale"].default_value = squares
    ck.inputs["Color1"].default_value = (1, 1, 1, 1)
    ck.inputs["Color2"].default_value = (0, 0, 0, 1)
    nt.links.new(tc.outputs["UV"], ck.inputs["Vector"])
    nt.links.new(ck.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    ob.data.materials.append(mat)
    return ob


def camera(lens=50.0, sensor=36.0):
    cd = bpy.data.cameras.new("Cam")
    cd.lens = lens
    cd.sensor_width = sensor
    ob = bpy.data.objects.new("Cam", cd)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.scene.camera = ob
    bpy.context.view_layer.objects.active = ob
    return ob


def render(name):
    sc = bpy.context.scene
    path = os.path.join(TMP, name + ".exr")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return load(path)


def load(path):
    im = bpy.data.images.load(path, check_existing=False)
    a = np.array(im.pixels[:], dtype=np.float32).reshape(im.size[1], im.size[0], im.channels)
    bpy.data.images.remove(im)
    return a  # row 0 = bottom


def decode_dirs(img):
    d = img[..., :3].astype(np.float64) * 2.0 - 1.0
    return d / np.linalg.norm(d, axis=-1, keepdims=True)
