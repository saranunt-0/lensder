# Module: core
<!-- lensder/core: pure maths shared by the shader, the compositor path and the overlay -->

---

## [2026-10-16] Lens maths core

**Type**: `feature`
**Status**: `resolved`

### Work Done
- `schema.py` is the single parameter table for all seven models (NONE,
  BROWN, DIVISION, NUKE, TDE_RADIAL, TDE_ANAMORPHIC, FISHEYE) plus common
  terms (centre, breathing, vignetting).
- `lens.py` does the vectorised film↔ray mapping, with an active-set Newton
  inverse.
- `presets.py` defines 14 bodies and 21 lenses, compiled to simple-expression
  driver strings that the same code evaluates in Python.
- `stmap.py` bakes on a 4-px lattice with bilinear upsampling; unsafe cells
  and all fisheyes are evaluated exactly.
- `osl.py` generates the shader parameter list from the schema.

### Bugs found by tests (root causes)
1. **Newton accepted folded solutions.** For impossible barrel strengths it
   converged to the mirrored root on the opposite side of the centre.
   Fix: require same side (q_u·q_d ≥ 0) and a positive Jacobian, in both NumPy
   and OSL.
2. **ST-map bake took 4.7 s at HD.** The full-grid Newton kept iterating
   converged points. Fix: active set plus lattice bake, now 0.3–0.9 s.
3. **Lattice interpolation broke for fisheyes.** The pinhole coordinate
   diverges near 90°, so interpolation is invalid there.
   Fix: always evaluate fisheyes exactly, and mark lattice cells with
   off-frame or invalid samples as unsafe and evaluate those exactly.

### Test-expectation corrections (code was right)
- 2× squeeze on a 4:3 gate delivering 2.39:1 is *height*-limited (2.67 > 2.39).
- DOF reference values were recomputed from the textbook formula (2.73 / 3.33 m).

### Checklist
- [x] 64 pure-Python tests (`python -m pytest tests --ignore=tests/blender`)
