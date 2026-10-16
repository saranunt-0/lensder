"""App handlers.

The Cycles ray-traced path needs no handlers, because drivers do the work.
Handlers cover what drivers cannot:

* scene-level changes (active camera, engine, device, resolution) that switch
  the backend or the sensor fit,
* re-baking the compositor ST-map when the lens changes, or on frame change
  when it is animated,
* refreshing the shader after an add-on update (load_post).
"""

import bpy
from bpy.app.handlers import persistent

from . import backend_osl, overlay, sync

_fingerprints = {}
_pending = set()


def _scene_fingerprint(scene):
    cam_ob = scene.camera
    r = scene.render
    cyc = getattr(scene, "cycles", None)
    cam_fp = None
    if cam_ob is not None and cam_ob.type == "CAMERA" and sync.is_active(cam_ob.data):
        cam = cam_ob.data
        model, params = sync.model_and_params(cam)
        cam_fp = (cam_ob.name, sync.focal(cam), sync.fstop(cam), cam.dof.use_dof, round(sync.focus_distance(cam_ob), 5),
                  cam.lensder.mode, cam.lensder.method, model, tuple(params.values()))
    return (cam_fp, r.engine, getattr(cyc, "device", None), r.resolution_x, r.resolution_y,
            r.resolution_percentage, r.pixel_aspect_x, r.pixel_aspect_y, r.fps, r.fps_base)


def _sync_scene(scene, rebake=True):
    cam_ob = scene.camera
    if cam_ob is not None and cam_ob.type == "CAMERA" and getattr(cam_ob.data, "lensder", None):
        sync.apply(cam_ob, scene, rebake=rebake)
        overlay.invalidate()


def _debounced(scene_name):
    _pending.discard(scene_name)
    scene = bpy.data.scenes.get(scene_name)
    if scene is not None:
        _sync_scene(scene)
    return None


@persistent
def on_depsgraph_update(scene, depsgraph):
    if sync._BUSY:
        return
    fp = _scene_fingerprint(scene)
    if _fingerprints.get(scene.name) == fp:
        return
    _fingerprints[scene.name] = fp
    if scene.name not in _pending and not bpy.app.background:
        # Debounce: dragging a slider fires many updates; re-bake once it settles.
        _pending.add(scene.name)
        bpy.app.timers.register(lambda n=scene.name: _debounced(n), first_interval=0.15)
    elif bpy.app.background:
        _sync_scene(scene)


@persistent
def on_frame_change(scene, depsgraph=None):
    """Animated lenses with the post-process path need a fresh ST-map every frame.

    Uses the depsgraph passed by Blender. During an animation render that is the
    render depsgraph, whose evaluated values are not flushed to the originals.
    """
    if sync._BUSY:
        return
    cam_ob = scene.camera
    if cam_ob is None or cam_ob.type != "CAMERA" or getattr(cam_ob.data, "lensder", None) is None:
        return
    known = _fingerprints.get(scene.name)
    if known is None or known[0] is None or known[0][0] != cam_ob.name:
        # Camera switch (timeline markers) or first frame: full sync.
        _fingerprints[scene.name] = _scene_fingerprint(scene)
        _sync_scene(scene)
    sync.update_frame(cam_ob, scene, depsgraph)


@persistent
def on_load(*_args):
    _fingerprints.clear()
    backend_osl.refresh_all_shaders()
    overlay.invalidate()


HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update),
    (bpy.app.handlers.frame_change_post, on_frame_change),
    (bpy.app.handlers.load_post, on_load),
)


def register():
    for lst, fn in HANDLERS:
        if fn not in lst:
            lst.append(fn)


def unregister():
    for lst, fn in HANDLERS:
        if fn in lst:
            lst.remove(fn)
