# Lensder — Research Notes

This file records what was researched before building Lensder, what was verified,
and how each finding shaped the design. Claims are tagged:

- **[verified]**: checked directly against Blender 5.2.2 (headless `bpy` build) or
  against source code.
- **[sourced]**: taken from published documentation or open-source code (cited).
- **[approx]**: a deliberate engineering approximation (explained).

---

## 1. How real lenses bend the image

### 1.1 Pinhole baseline

An ideal ("rectilinear") lens maps a ray at angle θ from the optical axis to a
point at distance `r = f · tan θ` from the image centre. Blender's default
*Perspective* camera is exactly this model. Every real lens deviates from it.
Those deviations are called *distortion*.

### 1.2 Radial distortion (spherical lenses)

The deviation depends mainly on distance from the centre:

- **Barrel**: the image edges are pulled inward and straight lines bow outward.
  This is typical of wide-angle lenses.
- **Pincushion**: the edges are pushed outward and straight lines bow inward.
  This is typical of telephoto lenses.
- **Mustache (complex)**: barrel near the centre and pincushion near the edges.
  You get it when terms of opposite sign are mixed. Common on ultra-wide primes.

Industry models (all supported by Lensder):

| Model | Direction | Normalisation | Formula (normalised coords) | Source |
|---|---|---|---|---|
| **Brown–Conrady** | undistorted → distorted | `x/f` (focal-normalised) | `xd = x(1+k1r²+k2r⁴+k3r⁶+k4r⁸) + p1(r²+2x²) + 2p2xy` | Blender/libmv `ApplyBrownDistortionModel` [sourced] |
| **Division** | undistorted → distorted | `x/f` | `xd = x / (1+k1r²+k2r⁴)` | libmv `ApplyDivisionDistortionModel` [sourced] |
| **Nuke** | distorted → undistorted | `x / (max(W,H)/2)` | `xu = xd/(1+k1rd²+k2rd⁴+p1yd²)`, `yu = yd/(1+k1rd²+k2rd⁴+p2xd²)` | libmv `InvertNukeDistortionModel` [sourced] |
| **3DE4 Radial Standard Deg 4** | distorted → undistorted | half-diagonal = 1 | radial `c2,c4` + decentering `u1,v1,u3,v3` + cylindric `phi,b` | Natron `DistortionModel3DEStandard` [sourced] |

Notes:

- Blender's own Movie Clip tracker (libmv) uses Brown, Polynomial (Brown without
  tangential terms), Divisions and Nuke. Lensder follows the **same conventions**,
  so coefficients solved in Blender's tracker can be typed straight into
  Lensder's Advanced mode.
- **The p1/p2 order is swapped between Blender and OpenCV.** Blender's x-term is
  `p1(r²+2x²)`. OpenCV's x-term is `p2(r²+2x²)`. [sourced: libmv code vs OpenCV docs]
- Brown is defined in the *undistorted → distorted* direction. A camera needs
  the opposite direction (it must ask "which ray lands on this pixel?"). So the
  inverse is solved numerically with Newton's method. Blender's own
  `advanced_camera.osl` template does the same. [verified]

### 1.3 Anamorphic lenses

An anamorphic lens has a cylindrical element (usually at the front). It
compresses the horizontal field by a **squeeze factor** `s`: typically 1.25×,
1.33×, 1.5×, 1.6×, 1.8× or 2×. The recorded image is later *desqueezed* (stretched
by `s`). [sourced: indepthcine, lensrentals]

Consequences, and how Lensder reproduces each one:

| Characteristic | Physical cause | Lensder implementation |
|---|---|---|
| Wider horizontal field of view | The horizontal focal length is effectively `f/s` | Effective sensor width = `s × physical width` with the true focal length (exact for a pinhole) |
| Vertically stretched oval bokeh | The cylinder gives different focal powers per axis; the cause is **not** the aperture shape [sourced: DIYPhotography / Filmmaker IQ] | `dof.aperture_ratio = s` (Blender's anamorphic bokeh control) |
| Characteristic barrel distortion, different per axis | Non-rotationally-symmetric optics | **3DE4 Anamorphic Standard** model (separate x/y polynomials with `cos 2φ`, `cos 4φ` terms) |
| Horizontal streak flares (often blue) | Internal reflections in cylindrical elements | Compositor Glare node, *Streaks* type, 2 streaks, 0°, tinted |
| "Mumps" (faces look wider at close focus) | The effective squeeze drops at close focus [sourced: EOSHD / D. Mullen; TVBEurope] | Documented; not modelled in v1 |
| Focus breathing that changes the aspect | Entrance pupils differ per axis [sourced: SynthEyes docs] | Uniform breathing only (v1) |

**3DE4 Anamorphic Standard Degree 4/6** (distorted → undistorted), as implemented
identically in two independent open-source projects (Natron `openfx-misc` and
AliceVision) [sourced]:

```
p    = (x / pa, y)                    # pixel-aspect removal
p    = R(-φ) p                        # lens rotation
xu   = x · (1 + Cx02 r² + Cx22 r² cos2θ + Cx04 r⁴ + Cx24 r⁴ cos2θ + Cx44 r⁴ cos4θ [+ deg-6 terms])
yu   = y · (1 + Cy02 r² + ... )
p    = (xu · pa · sqx, yu · sqy)
p    = R(+φ) p
```

with `r` normalised so that r = 1 at the half-diagonal of the (desqueezed) frame.
The degree-6 terms follow the same pattern: `C·06 r⁶`, `C·26 r⁶cos2θ`,
`C·46 r⁶cos4θ`, `C·66 r⁶cos6θ`. Lensder evaluates `r^m cos(nθ)` without
trigonometry, as `r^(m-n) · Re((x+iy)^n)`. This is exact and cheap in OSL.

> Caveat [approx]: 3DEqualizer's own filmback/pixel-aspect handling for
> anamorphic footage could not be verified, because the official LDPK PDF was
> unreachable from this environment. Lensder's normalisation matches AliceVision's.
> Coefficients exported from 3DE should be close, but they are **not guaranteed
> bit-identical**.

### 1.4 Fisheye lenses

Fisheyes do not follow `r = f tan θ`, so they can reach 180° and beyond. The
standard projections are [sourced: standard optics; Blender has two of them natively]:

| Projection | r(θ) | Typical use |
|---|---|---|
| Equidistant | `f · θ` | measurement and astronomy fisheyes |
| Equisolid angle | `2f · sin(θ/2)` | most photographic fisheyes |
| Stereographic | `2f · tan(θ/2)` | "flattering" fisheyes (e.g. Samyang 8mm) |
| Orthographic | `f · sin θ` | rare |

On top of these, the **Kannala–Brandt / OpenCV fisheye** polynomial
`θd = θ(1+k1θ²+k2θ⁴+k3θ⁶+k4θ⁸)` describes real deviations.

### 1.5 Other lens and camera behaviour

| Behaviour | Model used | Notes |
|---|---|---|
| **Vignetting** | `V(ρ) = 1 + v1ρ² + v2ρ⁴ + v3ρ⁶` (lensfun "pa" form) × optional natural `cos⁴θ` | ρ is normalised to the half-diagonal. Optical vignetting fades as you stop down [approx: 3 stops] |
| **Focus breathing** | thin-lens image distance `v = f·d/(d−f)` ⇒ `f_eff = f(1 + b·f/(d−f))` | `b = 1` is a unit-focusing thin lens; modern lenses are roughly 0–0.5, and negative values mean the frame widens at close focus [approx] |
| **Lateral chromatic aberration** | per-channel magnification (radial dispersion) | Done in the compositor (noise-free). A ray-traced version would multiply noise ×3 |
| **Depth of field** | thin lens: aperture radius `f/(2N)`; focus plane at distance `d` | Matches Cycles' perspective camera exactly [verified] |

### 1.6 Measured magnitudes (for presets)

Real distortion profiles from the **lensfun** database (CC-BY-SA 3.0), used only
to calibrate how strong the preset archetypes should be [sourced]:

| Lens (lensfun) | Focal | ptlens a, b, c | Character |
|---|---|---|---|
| Canon EF 24-70mm f/2.8L II | 24 | 0.0296, −0.0785, 0.0294 | mild mustache barrel |
| Canon EF 24-70mm f/2.8L II | 70 | 0.0091, −0.0202, 0.0231 | slight pincushion |
| Canon EF 16-35mm f/2.8L III | 16 | 0.0104, −0.0276, −0.0035 | barrel |
| Samyang 14mm f/2.8 | 14 | 0.0402, −0.0704, −0.0547 | strong mustache |
| Canon EF 85mm f/1.8 | 85 | −0.0010, −0.0023, 0 | ~none |

Lensder's presets are **generic archetypes**, such as "Standard zoom 24–70mm".
They are not brand profiles: their coefficients are fitted to match these
magnitudes, and the fit is shown in `tests/test_presets.py`.

### 1.7 Sensor sizes (camera body presets)

| Body / format | Size (mm) | Status |
|---|---|---|
| Full frame | 36.0 × 24.0 | standard |
| APS-C (Sony/Nikon/Fuji) | 23.5 × 15.6 | [sourced] |
| APS-C (Canon) | 22.3 × 14.9 | [sourced; some list 14.8] |
| Micro Four Thirds | 17.3 × 13.0 | [sourced] |
| 1-inch type | 13.2 × 8.8 | standard |
| Smartphone main (1/1.3″ type) | ≈ 9.8 × 7.3 | [approx] |
| ARRI ALEXA 35 (Open Gate) | 27.99 × 19.22 | [sourced: Wikipedia, rental listings] |
| ARRI ALEXA Mini LF (Open Gate) | 36.70 × 25.54 | [sourced] |
| Sony VENICE 2 (8.6K FF) | 35.9 × 24.0 | [sourced; Sigma lists 35.94 × 23.96] |
| RED V-RAPTOR 8K VV | 40.96 × 21.60 | [sourced] |
| Blackmagic Pocket 6K | 23.10 × 12.99 | [sourced] |
| Super 35 film (4-perf full aperture) | 24.89 × 18.66 | [sourced] |
| 35mm anamorphic (Scope camera aperture) | ≈ 21.95 × 18.6 | [approx] |
| Super 16 | 12.52 × 7.41 | standard |

---

## 2. What Blender 5.2 can actually do

All of the following was verified by running Blender **5.2.2 LTS** as a Python
module (`pip install bpy==5.2.2`), headless, with Cycles on CPU.

### 2.1 Cycles custom OSL cameras (the key enabler)

- `Camera.type = 'CUSTOM'`, `custom_mode = 'INTERNAL'`, `custom_shader = <Text>`.
  Assigning the shader compiles it, and its parameters appear as ID properties in
  `camera.cycles_custom`. The compiled bytecode is stored in the `.blend`, so
  render farms do not need the add-on to render it. [verified]
- Attributes available to the shader [verified from the Cycles source and the
  5.2 binary]: `cam:sensor_size` (the frame size after sensor fit and aspect),
  `cam:image_resolution`, `cam:focal_distance`, `cam:aperture_size` (= 1/(2N) for
  custom cameras), `cam:aperture_position` (a sampled lens position),
  `cam:aperture_aspect_ratio`. **The focal length is not exposed**, so it is
  passed in as a parameter through a driver.
- `camera_shader_raster_position()` runs from 0 to 1 with y = 0 at the bottom. A
  minimal OSL pinhole matches Blender's Perspective camera within 7·10⁻⁶ mean
  pixel error. [verified, `tests/blender/`]
- **Devices:** CPU and OptiX only. CUDA, HIP, Metal and oneAPI do not run OSL
  cameras. [sourced: Blender PR #129495, 5.2 manual on OSL] Lensder therefore
  falls back to the compositor path automatically on those devices.
- In the viewport, the custom camera is used in *camera view* in Rendered
  shading mode. [sourced: `intern/cycles/blender/camera.cpp` maps the viewplane
  to the camera border]

### 2.2 Drivers for animation

Simple-expression drivers support `clamp`, `lerp`, `min`/`max` and conditionals,
and they evaluate **without** "Auto Run Python Scripts" being enabled. [verified]
Lensder drives every shader parameter from its own properties, so keyframed
zooms, focus pulls and distortion changes work in the viewport and in final
renders without frame-change handlers.

### 2.3 Compositor (5.x node-group API)

- `scene.node_tree` is gone. The compositor is a `CompositorNodeTree` node group
  assigned to `scene.compositing_node_group`, and it outputs through a
  `NodeGroupOutput`. [verified] [sourced: IfcOpenShell 5.x port notes]
- Many node options are now input sockets (menus).
- **Map UV**: UV is in [0,1], the origin is at the bottom-left, pixel centres sit
  at (i+0.5)/w, the 3rd component is a coverage mask (0 = transparent), and
  anything out of range is clipped. A 10-pixel shift test matched to 1e-9.
  [verified]
- The compositor output is always the render resolution. So overscan for barrel
  distortion is done by widening the render field of view, not by changing the
  resolution. [verified]
- Generated float images: set `colorspace_settings.is_data` **before** writing
  `pixels`. Changing the colourspace afterwards silently regenerates the buffer
  (pixels are lost). [verified]

### 2.4 Scene-level camera behaviour

| Real camera control | Blender 5.2 property | Verified behaviour |
|---|---|---|
| White balance (K) | `view_settings.use_white_balance`, `white_balance_temperature` | Setting 3200 K turns a daylight scene blue, like a real camera [verified] |
| Tint | `white_balance_tint` | Neutral is **10**; higher values are more magenta. Lensder shows `tint − 10` [verified] |
| Exposure | `view_settings.exposure` (stops) | — |
| Shutter | `render.motion_blur_shutter` (fraction of a frame) | shutter = t · fps, or angle/360 |
| Aperture and bokeh | `dof.aperture_fstop`, `aperture_blades`, `aperture_ratio` | — |

These belong to the **scene**, not the camera, so Lensder applies them from the
scene's *active* camera.

---

## 3. Design decisions that came out of the research

1. **Two distortion backends, one maths core.**
   - *Ray-traced* (Cycles OSL camera): exact, with no overscan, correct with DOF,
     and visible in the Rendered viewport.
   - *Post-process* (compositor ST-map): works with EEVEE and on any GPU. It needs
     overscan, which costs a little resolution.
   - Both run the same equations as the pure-Python core, and the tests
     cross-check all three against each other.
2. **Advanced mode is a superset of Preset mode.** A preset compiles into the same
   model parameters that Advanced mode edits. Switching to Advanced keeps the
   current look and unlocks the raw coefficients.
3. **Exposure triangle (yes, but optional).** Real cameras darken when you stop
   down. Most CG users change the f-stop only for depth of field. Lensder offers
   two settings:
   - *Auto exposure* (the default): f-stop, shutter and ISO do not change
     brightness, like a camera in auto mode.
   - *Manual*: a real exposure triangle, measured in stops relative to a
     "metered" reference you set.

   Absolute photometric calibration is not attempted, because Blender's light
   units make it scene-dependent [approx].
4. **Zoom = focal length.** On a real camera, "zoom" is just focal length within
   the lens's range. Preset zoom lenses restrict the focal length to their range
   and interpolate their distortion across it, as real zooms go from barrel to
   pincushion.
5. **The viewport is honest about its limits.** Blender cannot distort Solid-mode
   geometry. Lensder draws a distorted grid and the frame outline as an overlay,
   so framing is truthful in every shading mode.

## 4. Not modelled (v1) — candidates for later

- Anamorphic mumps (squeeze that varies with focus) and per-axis breathing
- Rolling shutter per camera body (Cycles supports it: `cycles.rolling_shutter_*`)
- ISO noise and grain, film halation
- Exact import of 3DE `.txt`, Nuke `.nk` and lensfun XML profiles
- Viewport compositor behaviour: not testable headless (no GPU). See README

## 5. Sources

- Blender source (GitHub mirror):
  - `intern/cycles/blender/camera.cpp`
  - `intern/cycles/kernel/osl/services_shared.h`
  - `intern/libmv/libmv/simple_pipeline/distortion_models.h`
- Blender 5.2 bundled templates: `scripts/templates_osl/basic_camera.osl`,
  `advanced_camera.osl`
- [Blender manual: Custom Camera (5.2)](https://docs.blender.org/manual/en/latest/render/cycles/osl/camera.html)
- [Blender PR #129495: Cycles custom OSL cameras](https://projects.blender.org/blender/blender/pulls/129495)
- [Cycles OSL Camera feedback thread](https://devtalk.blender.org/t/cycles-osl-camera-feedback/38039)
- [Natron openfx-misc: Distortion/DistortionModel.cpp](https://github.com/NatronGitHub/openfx-misc) (3DE models)
- [AliceVision: camera/Undistortion3DEA4.cpp](https://github.com/alicevision/AliceVision)
- [Unreal Engine AnamorphicDistortionParameters](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AnamorphicDistortionParameters)
- [lensfun database](https://github.com/lensfun/lensfun) (CC-BY-SA 3.0; magnitudes only)
- [IfcOpenShell: Blender 5.x compositor port](https://dev.glassvision.dedyn.io/Glassvision/IfcOpenShell/commit/3fddc3523c6fed0d655d42766278d9b289b75834)
- [In Depth Cine: anamorphic squeeze](https://www.indepthcine.com/videos/anamorphic-squeeze)
- [DIYPhotography: why anamorphic bokeh is oval](https://www.diyphotography.net/this-is-why-anamorphic-lenses-have-oval-bokeh-its-nothing-to-do-with-the-aperture/)
- [SynthEyes: about anamorphic distortion](https://borisfx.com/documentation/syntheyes/syntheyesum_files/about_anamorphic_distortion/)
- [EOSHD: mumps discussion](https://www.eoshd.com/comments/topic/27172-mumps-with-the-kowa-16h8zbh/)
- Sensor sizes: [Sigma illumination guides](https://www.sigma-global.com/en/support/download/Illumination%20Guide_Aizu%20Prime_21mm_ver1.0.pdf), [Arri Alexa (Wikipedia)](https://wikipedia.com/wiki/ArriRaw), [lapseoftheshutter](https://www.lapseoftheshutter.com/aps-c-vs-super-35/)
