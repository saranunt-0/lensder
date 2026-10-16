"""Post-process lens: compositor ST-map distortion plus lens post effects.

Per scene, Lensder owns one node group, ``Lensder FX · <scene>``. It is
inserted just before the Group Output of the scene's compositor tree, and
the user's own nodes are left untouched:

    Image ─► Map UV (ST-map) ─► × vignette ─► chromatic aberration ─► streak flare ─► Image

* Distortion and vignetting stages run only for the post-process path. The
  ray-traced camera already does both.
* Chromatic aberration and flares run for both paths, because they are
  noise-free in post.

Verified on Blender 5.2: Map UV takes UV in [0,1] with a bottom-left origin,
and its 3rd component is a coverage mask. The compositor output is always
the render size.
"""

import bpy

from .core import stmap as S

NODE_NAME = "Lensder"
GROUP_VERSION = 2


def _group_name(scene):
    return f"Lensder FX · {scene.name}"


def _image_name(scene):
    return f"Lensder ST-Map · {scene.name}"


def _sock(sockets, identifier):
    """Socket by identifier (``sockets[...]`` looks up display names, which the
    Mix node repeats for each data type)."""
    return next(s for s in sockets if s.identifier == identifier)


def ensure_group(scene):
    name = _group_name(scene)
    grp = bpy.data.node_groups.get(name)
    if grp is not None and grp.get("lensder_version") == GROUP_VERSION:
        return grp
    if grp is None:
        grp = bpy.data.node_groups.new(name, "CompositorNodeTree")
    grp.nodes.clear()
    grp.interface.clear()
    grp.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    grp.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    n = grp.nodes
    gin = n.new("NodeGroupInput")
    gout = n.new("NodeGroupOutput")
    st_img = n.new("CompositorNodeImage")
    st_img.name = "STMap"
    scale = n.new("CompositorNodeScale")
    scale.name = "STScale"
    scale.inputs["Type"].default_value = "Render Size"
    scale.inputs["Frame Type"].default_value = "Stretch"
    sep = n.new("CompositorNodeSeparateColor")
    sep.name = "STSeparate"
    mapuv = n.new("CompositorNodeMapUV")
    mapuv.name = "Distort"
    mapuv.inputs["Interpolation"].default_value = "Bicubic"
    # Coverage comes from the ST-map's mask; "Clip" would also fade bicubic taps
    # that reach past the (overscanned) render border.
    mapuv.inputs["Extension X"].default_value = "Extend"
    mapuv.inputs["Extension Y"].default_value = "Extend"
    vig = n.new("ShaderNodeMix")
    vig.name = "Vignette"
    vig.data_type = "RGBA"
    vig.blend_type = "MULTIPLY"
    _sock(vig.inputs, "Factor_Float").default_value = 1.0
    ca = n.new("CompositorNodeLensdist")
    ca.name = "Chromatic"
    ca.inputs["Type"].default_value = "Radial"
    ca.inputs["Distortion"].default_value = 0.0
    flare = n.new("CompositorNodeGlare")
    flare.name = "Flare"
    flare.inputs["Type"].default_value = "Streaks"
    flare.inputs["Streaks"].default_value = 2
    flare.inputs["Streaks Angle"].default_value = 0.0
    flare.inputs["Tint"].default_value = (0.55, 0.75, 1.0, 1.0)
    flare.inputs["Highlights Threshold"].default_value = 1.0

    links = grp.links
    links.new(st_img.outputs["Image"], scale.inputs["Image"])
    links.new(gin.outputs[0], mapuv.inputs["Image"])
    links.new(scale.outputs["Image"], mapuv.inputs["UV"])
    links.new(scale.outputs["Image"], sep.inputs["Image"])
    links.new(mapuv.outputs["Image"], _sock(vig.inputs, "A_Color"))
    links.new(sep.outputs["Alpha"], _sock(vig.inputs, "B_Color"))
    links.new(_sock(vig.outputs, "Result_Color"), ca.inputs["Image"])
    links.new(ca.outputs["Image"], flare.inputs["Image"])
    links.new(flare.outputs["Image"], gout.inputs[0])
    for i, node in enumerate((gin, st_img, scale, sep, mapuv, vig, ca, flare, gout)):
        node.location = (i * 200, 0)
    grp["lensder_version"] = GROUP_VERSION
    return grp


def _scene_tree(scene, create):
    tree = scene.compositing_node_group
    if tree is None and create:
        tree = bpy.data.node_groups.new(f"Compositing · {scene.name}", "CompositorNodeTree")
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = tree.nodes.new("CompositorNodeRLayers")
        rl.scene = scene
        out = tree.nodes.new("NodeGroupOutput")
        out.location = (400, 0)
        tree.links.new(rl.outputs["Image"], out.inputs[0])
        scene.compositing_node_group = tree
    return tree


def _group_node(tree):
    return tree.nodes.get(NODE_NAME) if tree is not None else None


def _insert_group_node(scene, tree, grp):
    node = tree.nodes.new("CompositorNodeGroup")
    node.name = NODE_NAME
    node.label = "Lensder"
    node.node_tree = grp
    out = next((nd for nd in tree.nodes if nd.bl_idname == "NodeGroupOutput"), None)
    if out is None:
        out = tree.nodes.new("NodeGroupOutput")
        if not tree.interface.items_tree:
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    target = out.inputs[0]
    if target.is_linked:
        src = target.links[0].from_socket
        tree.links.remove(target.links[0])
    else:
        rl = next((nd for nd in tree.nodes if nd.bl_idname == "CompositorNodeRLayers"), None)
        if rl is None:
            rl = tree.nodes.new("CompositorNodeRLayers")
            rl.scene = scene
        src = rl.outputs["Image"]
    tree.links.new(src, node.inputs[0])
    tree.links.new(node.outputs[0], target)
    node.location = (out.location.x - 220, out.location.y)
    out.location.x += 60
    return node


def disable(scene):
    node = _group_node(scene.compositing_node_group)
    if node is not None and not node.mute:
        node.mute = True


def sync(scene, cam_ob, state, path, overscan, rebake=True):
    """Configure the post stages for the scene camera; ``path`` is the distortion backend."""
    st = cam_ob.data.lensder
    distort = path == "COMPOSITOR"
    post = st.chromatic > 0.0 or st.flare
    tree = scene.compositing_node_group
    if not (distort or post):
        disable(scene)
        return
    tree = _scene_tree(scene, create=True)
    grp = ensure_group(scene)
    node = _group_node(tree) or _insert_group_node(scene, tree, grp)
    if node.node_tree != grp:
        node.node_tree = grp
    if node.mute:
        node.mute = False
    if not scene.render.use_compositing:
        scene.render.use_compositing = True

    n = grp.nodes
    for name in ("Distort", "Vignette"):
        if n[name].mute == distort:
            n[name].mute = not distort
    ca = n["Chromatic"]
    ca.mute = st.chromatic <= 0.0
    # Lens Distortion "Dispersion" is a radial per-channel scale; 0.05 is already strong.
    ca.inputs["Dispersion"].default_value = st.chromatic * 0.05
    flare = n["Flare"]
    flare.mute = not st.flare
    flare.inputs["Strength"].default_value = st.flare_strength

    if distort and rebake:
        bake_stmap(scene, state, overscan)


_baked = {}  # image name -> key of the lens state currently in its pixels


def bake_stmap(scene, state, overscan, force=False):
    r = scene.render
    w = max(2, int(r.resolution_x * r.resolution_percentage / 100))
    h = max(2, int(r.resolution_y * r.resolution_percentage / 100))
    name = _image_name(scene)
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) != (w, h):
        bpy.data.images.remove(img)
        img = None
    key = (state.model, state.focal, state.focus_distance, state.frame_w, state.frame_h,
           tuple(state.params.values()), overscan, w, h)
    if img is not None and not force and _baked.get(name) == key:
        return img
    if img is None:
        img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
        # Must be set before writing pixels; changing it later regenerates the buffer.
        img.colorspace_settings.is_data = True
    data = S.bake(state, w, h, overscan)
    img.pixels.foreach_set(data.ravel())
    img.update()
    _baked[name] = key
    grp = bpy.data.node_groups.get(_group_name(scene))
    if grp is not None:
        node = grp.nodes["STMap"]
        if node.image != img:
            node.image = img
    return img
