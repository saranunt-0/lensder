"""Render the README sample images (Cycles CPU, ray-traced lens).

Usage:
    blender -b -P scripts/render_samples.py -- OUT_DIR
    python scripts/render_samples.py OUT_DIR        (with the ``bpy`` pip module)
"""

import glob
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy  # noqa: E402

for _p in glob.glob(os.path.join(bpy.utils.resource_path("LOCAL"), "python", "lib", "python3*", "site-packages")):
    if _p not in sys.path:
        sys.path.append(_p)  # oslquery for the pip bpy module

import addon_utils  # noqa: E402

import lensder  # noqa: E402
from lensder import idprops  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OUT = os.path.abspath(argv[0] if argv else "samples")
os.makedirs(OUT, exist_ok=True)


def material(name, color, emission=0.0, checker=None):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.6
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1)
        bsdf.inputs["Emission Strength"].default_value = emission
    if checker:
        ck = nt.nodes.new("ShaderNodeTexChecker")
        ck.inputs["Scale"].default_value = checker
        ck.inputs["Color1"].default_value = (0.85, 0.85, 0.8, 1)
        ck.inputs["Color2"].default_value = (0.08, 0.09, 0.12, 1)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(tc.outputs["UV"], ck.inputs["Vector"])
        nt.links.new(ck.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def build_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("cycles", default_set=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    sc.render.compositor_device = "CPU"  # headless machines have no GPU context
    sc.render.resolution_x, sc.render.resolution_y = 480, 270
    sc.render.image_settings.file_format = "PNG"
    sc.view_settings.view_transform = "AgX"
    world = bpy.data.worlds.new("w")
    sc.world = world
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.35, 0.4, 0.5, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
    # Grid walls: straight lines make distortion obvious.
    for loc, rot, size in (((0, 8, 2.5), (math.pi / 2, 0, 0), 30), ((0, 0, 0), (0, 0, 0), 30),
                           ((-6, 0, 2.5), (math.pi / 2, 0, math.pi / 2), 30)):
        bpy.ops.mesh.primitive_plane_add(size=size, location=loc, rotation=rot)
        bpy.context.active_object.data.materials.append(material("grid", (1, 1, 1), checker=30))
    # Subject at the focus distance
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.6, location=(0.4, 2.2, 0.6), segments=48, ring_count=24)
    bpy.ops.object.shade_smooth()
    bpy.context.active_object.data.materials.append(material("subject", (0.8, 0.25, 0.1)))
    bpy.ops.mesh.primitive_cube_add(size=0.8, location=(-1.6, 3.2, 0.4), rotation=(0, 0, 0.5))
    bpy.context.active_object.data.materials.append(material("cube", (0.15, 0.4, 0.8)))
    # Small bright lights behind the subject: bokeh and flares
    for i, (x, z) in enumerate(((-1.4, 1.05), (-0.6, 1.4), (0.2, 1.15), (0.95, 1.45), (1.6, 1.0))):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.035, location=(x, 6.5, z))
        hue = ((1.0, 0.8, 0.5), (0.6, 0.8, 1.0), (1.0, 0.95, 0.9), (1.0, 0.6, 0.4), (0.8, 0.9, 1.0))[i]
        bpy.context.active_object.data.materials.append(material(f"bulb{i}", hue, emission=400.0))
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = 3.0
    sob = bpy.data.objects.new("sun", sun)
    sob.rotation_euler = (math.radians(50), 0, math.radians(30))
    sc.collection.objects.link(sob)
    cd = bpy.data.cameras.new("Camera")
    cam = bpy.data.objects.new("Camera", cd)
    cam.location = (0.2, -2.6, 1.2)
    cam.rotation_euler = (math.radians(86), 0, 0)
    sc.collection.objects.link(cam)
    sc.camera = cam
    bpy.context.view_layer.objects.active = cam
    return sc, cam


SAMPLES = [
    # file, label, setup(cam_data, scene)
    ("01_blender_35mm", "Blender camera, 35 mm", dict(mode="NATIVE", lens=35)),
    ("02_ultrawide_14_mustache", "Ultra-wide 14 mm, mustache", dict(lens_preset="UW14_MUSTACHE")),
    ("03_zoom_24", "24-70 zoom @ 24 mm (barrel)", dict(lens_preset="Z24_70", focal=24)),
    ("04_zoom_70", "24-70 zoom @ 70 mm (pincushion)", dict(lens_preset="Z24_70", focal=70)),
    ("05_anamorphic_2x", "Anamorphic 2x 75 mm T2.3 @1.5 m: oval bokeh, flares",
     dict(lens_preset="ANA2X75", body="ALEXA35", dof=2.3, near_subject=True, flare=True, res=(480, 201))),
    ("06_portrait_85_dof", "85 mm f/1.4 @1.5 m: round bokeh (compare with 05)",
     dict(lens_preset="P85", dof=1.4, focal=85, near_subject=True)),
    ("07_fisheye_circular", "Circular fisheye 8 mm", dict(lens_preset="FISH8")),
    ("08_fisheye_fullframe", "Full-frame fisheye 15 mm", dict(lens_preset="FISH15")),
    ("09_vintage_58", "Vintage 58 mm f/2 (vignette, fringing)", dict(lens_preset="V58", chromatic=0.8, dof=2.0)),
]


def render_all():
    out = []
    for name, label, cfg in SAMPLES:
        sc, cam_ob = build_scene()
        cam = cam_ob.data
        st = cam.lensder
        res = cfg.get("res", (480, 270))
        sc.render.resolution_x, sc.render.resolution_y = res
        if cfg.get("mode") == "NATIVE":
            cam.lens = cfg["lens"]
        else:
            cam.lens = cfg.get("focal", 35)
            st.mode = "PRESET"
            if "body" in cfg:
                st.body = cfg["body"]
            st.lens_preset = cfg["lens_preset"]
            if "focal" in cfg:
                idprops.set(cam, "focal", cfg["focal"])
            if "dof" in cfg:
                cam.dof.use_dof = True
                target = bpy.data.objects["Sphere"]
                if cfg.get("near_subject"):
                    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.07, location=(0.25, -1.1, 1.08),
                                                         segments=48, ring_count=24)
                    bpy.ops.object.shade_smooth()
                    target = bpy.context.active_object
                    target.data.materials.append(material("near", (0.9, 0.75, 0.2)))
                    bpy.context.view_layer.objects.active = cam_ob
                cam.dof.focus_object = target
                idprops.set(cam, "fstop", cfg["dof"])
            st.chromatic = cfg.get("chromatic", 0.0)
            st.flare = cfg.get("flare", False)
            st.flare_strength = 0.5
            st.method = "RAYTRACE"
        sc.render.filepath = os.path.join(OUT, name + ".png")
        bpy.ops.render.render(write_still=True)
        out.append((sc.render.filepath, label))
        print("rendered", name)
    return out


def contact_sheet(items, path, cols=3, cell=(480, 270), pad=10, label_h=26):
    from PIL import Image, ImageDraw
    rows = math.ceil(len(items) / cols)
    W = cols * cell[0] + (cols + 1) * pad
    H = rows * (cell[1] + label_h) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for i, (img_path, label) in enumerate(items):
        im = Image.open(img_path).convert("RGB")
        r, c = divmod(i, cols)
        x = pad + c * (cell[0] + pad)
        y = pad + r * (cell[1] + label_h + pad)
        oy = (cell[1] - im.height) // 2
        sheet.paste(im, (x, y + oy))
        draw.text((x + 4, y + cell[1] + 6), label, fill=(230, 230, 230))
    sheet.save(path)


if __name__ == "__main__":
    lensder.register()
    items = render_all()
    try:
        contact_sheet(items, os.path.join(OUT, "contact_sheet.png"))
        print("contact sheet written")
    except ImportError:
        print("Pillow not installed: skipping contact sheet")
