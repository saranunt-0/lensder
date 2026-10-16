# Module: research
<!-- What was researched and verified before and during implementation -->

---

## [2026-10-16] Lens models and Blender 5.2 capabilities

**Type**: `investigation`
**Status**: `resolved`

### Context
The user asked for deep research on radial and anamorphic lenses before
building a Blender tool with three modes (native, preset, advanced). The
repository was empty.

### Work Done
- Installed Blender 5.2.2 LTS as a Python module (`pip install bpy`) to
  verify the API directly. docs.blender.org was unreachable through the
  proxy, but GitHub raw files were reachable.
- Read Cycles sources (`blender/camera.cpp`, `kernel/osl/services_shared.h`,
  `kernel/camera/camera.h`) to get the OSL camera attributes, the custom-camera
  sensor size and the aperture semantics.
- Cross-checked the 3DE4 models in two independent open-source
  implementations (Natron openfx-misc, AliceVision). Proved the compact polar
  form `r^(m-n)·Re(z^n)` equal to Natron's expanded coefficients.
- Read libmv for Blender's tracker conventions (Brown p1/p2 order, Nuke
  normalisation).
- Fitted lensfun PTLens profiles to calibrate preset magnitudes (in mm on the
  film, not focal-normalised; the latter explodes for telephotos).

### Checklist
- [x] OSL camera renders headless; minimal pinhole matches the Perspective camera (7e-6)
- [x] Map UV convention (UV [0,1], bottom-left, z = coverage) — 10 px shift test exact
- [x] White balance semantics (3200 K → blue; tint 10 = neutral)
- [x] Simple-expression drivers: clamp/lerp/log/pow are simple; `**` is not
- [x] `aperture_ratio` scales bokeh x by 1/ratio → ratio = squeeze gives vertical ovals
- [ ] OSL camera on OptiX — no GPU here (sourced: Blender PR #129495)

### Unverified Items
- [ ] Viewport compositor with the Lensder node group in a GPU Blender: expected
  to show the distorted image in camera view.
- [ ] OSL camera on OptiX: expected identical to CPU.

### Assumptions Made
- 3DE pixel-aspect handling follows AliceVision. The official LDPK PDF was
  unreachable.
