# Lensder architecture

## One maths core, three consumers

```
                 lensder/core  (no bpy, NumPy)
   schema.py ── single list of every lens parameter
   lens.py   ── film position <-> ray direction (reference implementation)
   presets.py── bodies/lenses -> simple-expression strings (also evaluated in Python)
   osl.py    ── generates the Cycles OSL camera from schema + same pipeline
   stmap.py  ── ST-map + overscan for the compositor path
   overlay_geom.py ── distorted grid polylines
        │                     │                         │
        ▼                     ▼                         ▼
 backend_osl.py        backend_comp.py              overlay.py
 (Cycles custom        (compositor node group,      (GPU draw handler,
  camera + drivers)     ST-map image)                camera view)
```

The tests check the consumers against each other:

| Test | What it checks | Accuracy |
|---|---|---|
| `tests/blender/test_osl_shader.py` | rendered rays vs `lens.film_to_ray` | < 0.02° |
| `tests/blender/test_addon.py` | post-process geometry vs the same reference | < 0.25 px |

## Where state lives (and why)

| Data | Storage | Reason |
|---|---|---|
| Mode, body, lens preset, method, toggles | `Camera.lensder` (bpy.props) | UI enums; not animated |
| Lens coefficients, amounts, focal, f-stop | hidden camera ID props `_lensder_*` | Animatable **and** present without the add-on. A driver whose target is missing evaluates to 0.0 (verified), so add-on props must never be driver targets |
| Shader parameters | `Camera.cycles_custom` (Cycles) | Driven from the ID props. Model and projection are written as plain values |
| Blender `lens`, `dof.aperture_fstop` | driven from `_lensder_focal/fstop` | Shader drivers read only ID props, so there is no dependency cycle |
| Exposure, WB, motion blur | scene, written statically | Scene-level; drivers would break on farms |

## Two lens backends

- **RAYTRACE**: `cam.type = 'CUSTOM'`, internal OSL text `lensder_camera.osl`,
  with the bytecode stored in the camera. Animation needs no handlers.
- **COMPOSITOR**: the camera stays a perspective camera with an overscanned
  sensor, and the per-scene node group `Lensder FX · <scene>` is inserted
  before Group Output. Two rules keep it correct:
  - The overscan is *stable*: the worst case over the zoom range and
    keyframes. Per-frame camera changes reach the render one frame late
    (verified).
  - The per-frame ST-map re-bake reads the **evaluated** camera from the
    handler's depsgraph. Render-time animation is not flushed to the originals
    (verified).

`AUTO` picks RAYTRACE for Cycles on CPU or OptiX, and COMPOSITOR otherwise.

## Coordinate conventions

- Film: mm on the desqueezed frame, origin at the centre, +x right, +y up.
- Camera space: +z forward (Cycles OSL camera convention, verified).
- Brown and Division are normalised by focal length. Nuke uses max(W,H)/2;
  3DE uses the half-diagonal.
- Brown/Division are defined undistorted → distorted, so the camera inverts
  them with Newton and rejects folds. Nuke/3DE are distorted → undistorted,
  which is direct.
- The distortion centre shifts only the distortion, not the optical axis
  (3DE convention).
