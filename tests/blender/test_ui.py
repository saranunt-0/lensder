"""Exercise every panel's draw code with a validating mock layout.

Headless Blender cannot draw UI, so a typo in a property path would only show
up when a user opens the panel. The mock checks every ``prop`` path and
operator id against the real RNA.
"""

import types

import pytest

from tests.blender import bhelpers as H

bpy = H.bpy

import lensder  # noqa: E402
from lensder import ui  # noqa: E402


class MockLayout:
    def __init__(self, log):
        self.log = log
        self.use_property_split = False
        self.use_property_decorate = False
        self.active = True
        self.enabled = True
        self.alignment = "EXPAND"

    def _child(self, *a, **k):
        return MockLayout(self.log)

    row = column = box = split = column_flow = grid_flow = _child

    def prop(self, data, path, **kw):
        if path.startswith("["):
            assert path[2:-2] in data.keys(), f"missing ID property {path} on {data!r}"
        else:
            assert hasattr(data, path) or path in data.bl_rna.properties, f"missing property {path} on {data!r}"
        self.log.append(("prop", path))

    def prop_search(self, data, path, search_data, search_prop, **kw):
        assert hasattr(data, path) and hasattr(search_data, search_prop)

    def operator(self, idname, **kw):
        cat, name = idname.split(".")
        assert hasattr(getattr(bpy.ops, cat), name), f"unknown operator {idname}"
        self.log.append(("op", idname))
        return types.SimpleNamespace()

    def label(self, **kw):
        self.log.append(("label", kw.get("text", "")))

    def separator(self, **kw):
        pass


@pytest.fixture(autouse=True)
def addon():
    H.reset()
    lensder.register()
    yield
    lensder.unregister()


def _context(ob, space_type):
    return types.SimpleNamespace(
        scene=bpy.context.scene, object=ob, active_object=ob, screen=None,
        space_data=types.SimpleNamespace(type=space_type),
    )


@pytest.mark.parametrize("mode", ["NATIVE", "PRESET", "ADVANCED"])
@pytest.mark.parametrize("space", ["VIEW_3D", "PROPERTIES"])
@pytest.mark.parametrize("lens", ["Z24_70", "ANA2X40", "FISH8"])
def test_all_panels_draw(mode, space, lens):
    ob = H.camera()
    st = ob.data.lensder
    st.mode = "PRESET"
    st.lens_preset = lens
    st.mode = mode
    st.exposure_mode = "MANUAL"
    st.use_exposure = st.use_shutter = st.use_white_balance = True
    ctx = _context(ob, space)
    log = []
    ui.draw_main(MockLayout(log), ctx, ob)
    if mode == "NATIVE":
        return
    for _sid, _label, drawer, header, modes, _closed in ui.SECTIONS:
        if mode in modes:
            drawer(MockLayout(log), ctx, ob)
            if header:
                header(MockLayout(log), ob)
    props = {p for kind, p in log if kind == "prop"}
    assert '["_lensder_focal"]' in props
    if mode == "ADVANCED":
        assert "model" in props
    if mode == "PRESET":
        assert '["_lensder_amount_distortion"]' in props


def test_panel_classes_registered_and_poll():
    ob = H.camera()
    for cls in ui.PANELS:
        assert hasattr(bpy.types, cls.__name__) or cls.is_registered
    ctx = _context(ob, "VIEW_3D")
    main = ui.PANELS[0]
    assert main.poll(ctx)
    ctx_none = _context(None, "VIEW_3D")
    assert not main.poll(ctx_none)
