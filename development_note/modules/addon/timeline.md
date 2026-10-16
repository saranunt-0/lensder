# Module: addon
<!-- lensder/*.py: Blender integration -->

---

## [2026-10-16] Add-on with Camera Kit / Advanced modes and two backends

**Type**: `feature` + `debug`
**Status**: `resolved` (GPU-only items unverified, see below)

### Work Done
- Properties, UI (3D-View sidebar + Camera properties), operators,
  handlers, viewport overlay.
- Ray-traced backend (OSL custom camera + drivers) and post-process backend
  (compositor ST-map).
- 50 headless Blender tests: shader vs reference, both backends, animation
  render, farm render without add-on, tracker import, UI draw validation,
  duplicate cameras, dependency cycles.

### Debugging log (each bug was reproduced, isolated, then fixed)
1. **Render farm without the add-on rendered no lens at all.**
   - Repro: save, unregister the add-on, reopen, render.
   - Isolation showed Blender evaluates a driver with an unresolvable target to
     **0.0 and overwrites the stored value**.
   - Fix: animatable values became hidden camera ID properties, and drivers
     read only those or native properties.
2. **Blender reported 8 dependency cycles.**
   - Cause: shader drivers read the camera's own `lens`/`aperture_fstop`, while
     the camera's parameter evaluation depends on its driven ID properties.
   - Fix: Lensder owns focal/f-stop as ID props and drives Blender's values from
     them. A subprocess regression test greps for "Dependency cycle".
3. **Camera Kit render zoomed ~7×.**
   - Isolation: constants rendered identically to drivers, so the drivers were
     cleared. Varying parameters one at a time pointed at breathing.
   - Root cause: with DOF off, Cycles reports focus distance ≈1e-5 m, not 0.
   - Fix: breathing (and DOF) only when the aperture is > 0 and the focus
     distance > 1 mm.
4. **Post-process animation frame 2 showed a mix of two frames.**
   - Instrumented the handlers. Two verified Blender behaviours were involved:
     - (a) Render-time animation is evaluated on copies and **not flushed** to
       the originals, so the handler saw frame 1's focal.
     - (b) Camera edits in `frame_change_post` reach the render **one frame
       late**.
   - Fix: a stable overscan (worst case over the zoom range and keyframes),
     plus per-frame ST-map baking from the handler's *evaluated* depsgraph.
5. Smaller fixes:
   - Mix-node sockets must be looked up by identifier.
   - Map UV "Clip" faded corner coverage, so it now uses "Extend".
   - Python ID-prop writes need `update_tag()`.
   - The manifest tagline exceeded 64 characters (caught by Blender's validator).
   - A lens guess picked a zoom over an exact prime.

### Design change made during implementation
- Presets no longer auto-enable chromatic aberration and flares. Doing so
  silently inserted nodes into the user's compositor, so post effects are now
  opt-in and the UI lists the lens' typical traits instead.

### Unverified Items (need a GPU Blender)
- [ ] Viewport overlay drawing (`overlay.py`). Expected: an orange distorted
  grid and a white frame in camera view. Errors print once to the console.
- [ ] Viewport compositor preview for the post-process path.
- [ ] OSL camera on OptiX.

### Assumptions Made
- `POLYLINE_UNIFORM_COLOR` exists in 5.x, with a `UNIFORM_COLOR` fallback.
