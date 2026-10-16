"""Lensder — real camera bodies and lenses for Blender.

Three modes per camera:

* **Blender**: the standard camera; Lensder does nothing.
* **Camera Kit**: pick a camera body and a lens preset (spherical, anamorphic,
  fisheye), then adjust it with a few physical controls.
* **Advanced**: full lens models (Brown–Conrady, Division, Nuke, 3DE4 Radial,
  3DE4 Anamorphic deg 4/6, fisheye) with every coefficient.

The pure maths lives in ``lensder.core`` and does not import ``bpy``.
"""

bl_info = {
    "name": "Lensder",
    "author": "Lensder contributors",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "3D Viewport > Sidebar > Lensder, and Properties > Camera",
    "description": "Real camera bodies and lenses: radial, anamorphic and fisheye distortion, "
                   "exposure triangle, shutter, white balance",
    "category": "Camera",
}

_modules = None


def _load():
    global _modules
    if _modules is None:
        from . import handlers, ops, overlay, props, ui
        _modules = (props, ops, ui, overlay, handlers)
    return _modules


def register():
    import bpy
    props, ops, ui, overlay, handlers = _load()
    props.register()
    for cls in ops.CLASSES + tuple(ui.PANELS):
        bpy.utils.register_class(cls)
    overlay.register()
    handlers.register()


def unregister():
    import bpy
    props, ops, ui, overlay, handlers = _load()
    handlers.unregister()
    overlay.unregister()
    for cls in reversed(ops.CLASSES + tuple(ui.PANELS)):
        bpy.utils.unregister_class(cls)
    props.unregister()
