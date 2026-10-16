"""Camera-body and lens presets, and how a preset becomes model parameters.

A lens preset is *compiled* into simple-expression driver strings, one per
shader parameter. The same strings are evaluated in Python
(:func:`evaluate`), so the viewport overlay, the compositor ST-map and the
Cycles shader always agree.

Expression variables:

* ``f``  — focal length (mm), read from ``Camera.lens``
* ``N``  — f-number, read from ``Camera.dof.aperture_fstop``
* ``md`` — distortion amount multiplier
* ``mv`` — vignetting amount multiplier
* ``mb`` — breathing amount multiplier

Spherical distortion is stored as the relative image-height error
``D(u) = a·u² + b·u⁴``, with ``u = 1`` at the full-frame corner (21.633 mm),
fitted to lensfun measurements (see docs/research.md). Converting to Brown
(focal-normalised) gives ``k1 = a·(f/21.633)²`` and ``k2 = b·(f/21.633)⁴``.
Because the distortion is defined on the film in millimetres, a smaller
sensor crops into the centre and shows less distortion, as it does in reality.

Anamorphic presets use the 3DE4 anamorphic model, which is relative to the
frame diagonal. They are stylised archetypes, not measured profiles.
"""

from dataclasses import dataclass, field
import math

from . import schema

FF_HALF_DIAGONAL = 0.5 * math.hypot(36.0, 24.0)  # 21.633 mm


@dataclass(frozen=True)
class Body:
    id: str
    label: str
    width: float
    height: float
    description: str


@dataclass(frozen=True)
class LensPreset:
    id: str
    label: str
    kind: str  # 'SPHERICAL' | 'ANAMORPHIC' | 'FISHEYE'
    focal_min: float
    focal_max: float
    max_aperture: float  # smallest f-number (wide open)
    blades: int
    description: str
    squeeze: float = 1.0
    dist_wide: dict = field(default_factory=dict)
    dist_tele: dict = field(default_factory=dict)
    vignette_ev: float = 0.0  # corner falloff wide open (negative EV)
    breathing: float = 0.0
    chromatic: float = 0.0  # suggested lateral CA amount (post-process)
    flare: bool = False
    close_focus: float = 0.5  # metres

    @property
    def is_zoom(self):
        return self.focal_max > self.focal_min


BODIES = (
    Body("FULL_FRAME", "Full Frame (36 × 24)", 36.0, 24.0, "35mm stills full frame"),
    Body("APSC", "APS-C (23.5 × 15.6)", 23.5, 15.6, "Sony, Nikon, Fujifilm APS-C"),
    Body("APSC_CANON", "APS-C Canon (22.3 × 14.9)", 22.3, 14.9, "Canon APS-C"),
    Body("MFT", "Micro Four Thirds (17.3 × 13.0)", 17.3, 13.0, "Panasonic, OM System"),
    Body("ONE_INCH", "1-inch (13.2 × 8.8)", 13.2, 8.8, "Compacts, drones"),
    Body("PHONE", "Smartphone 1/1.3\" (≈9.8 × 7.3)", 9.8, 7.3, "Typical flagship main camera (approx.)"),
    Body("ALEXA35", "ARRI ALEXA 35 Open Gate (27.99 × 19.22)", 27.99, 19.22, "Super 35 cinema"),
    Body("ALEXA_MINI_LF", "ARRI ALEXA Mini LF Open Gate (36.70 × 25.54)", 36.70, 25.54, "Large format cinema"),
    Body("VENICE2", "Sony VENICE 2 8.6K (35.9 × 24.0)", 35.9, 24.0, "Full frame cinema"),
    Body("VRAPTOR", "RED V-RAPTOR 8K VV (40.96 × 21.60)", 40.96, 21.60, "VistaVision cinema"),
    Body("BMPCC6K", "Blackmagic Pocket 6K (23.10 × 12.99)", 23.10, 12.99, "Super 35 16:9"),
    Body("SUPER35_FILM", "Super 35 Film 4-perf (24.89 × 18.66)", 24.89, 18.66, "35mm film full aperture"),
    Body("SCOPE_FILM", "35mm Anamorphic Film (≈21.95 × 18.6)", 21.95, 18.6, "Scope camera aperture (approx.)"),
    Body("SUPER16", "Super 16 Film (12.52 × 7.41)", 12.52, 7.41, "16mm film"),
)

BODY_BY_ID = {b.id: b for b in BODIES}


def _sph(a, b):
    return {"a": a, "b": b}


def _ana(scale, cx02, cy02, cx22=0.0, cy22=0.0, cx04=0.0, cy04=0.0):
    return {"cx02": cx02 * scale, "cy02": cy02 * scale, "cx22": cx22 * scale,
            "cy22": cy22 * scale, "cx04": cx04 * scale, "cy04": cy04 * scale}


LENSES = (
    # --- Spherical primes ---------------------------------------------------
    LensPreset("UW14_MUSTACHE", "Ultra-Wide 14mm f/2.8 (budget, mustache)", "SPHERICAL", 14, 14, 2.8, 7,
               "Strong mustache distortion typical of budget ultra-wides",
               dist_wide=_sph(-0.33, 0.26), vignette_ev=-2.0, breathing=0.2, chromatic=0.6, close_focus=0.28),
    LensPreset("UW14", "Ultra-Wide 14mm f/2.8 (corrected)", "SPHERICAL", 14, 14, 2.8, 6,
               "Well-corrected premium ultra-wide",
               dist_wide=_sph(-0.034, 0.022), vignette_ev=-1.8, breathing=0.1, chromatic=0.3, close_focus=0.2),
    LensPreset("W24", "Wide 24mm f/1.4", "SPHERICAL", 24, 24, 1.4, 9,
               "Fast wide prime, moderate barrel",
               dist_wide=_sph(-0.05, 0.02), vignette_ev=-2.0, breathing=0.3, chromatic=0.3, close_focus=0.25),
    LensPreset("N35", "Classic 35mm f/1.4", "SPHERICAL", 35, 35, 1.4, 9,
               "Street/documentary classic, mild barrel",
               dist_wide=_sph(-0.02, 0.006), vignette_ev=-1.7, breathing=0.3, chromatic=0.25, close_focus=0.3),
    LensPreset("N50", "Normal 50mm f/1.8", "SPHERICAL", 50, 50, 1.8, 7,
               "Everyday 'nifty fifty', slight mustache",
               dist_wide=_sph(0.036, -0.050), vignette_ev=-1.5, breathing=0.4, chromatic=0.2, close_focus=0.35),
    LensPreset("P85", "Portrait 85mm f/1.4", "SPHERICAL", 85, 85, 1.4, 9,
               "Portrait prime, almost no distortion",
               dist_wide=_sph(-0.010, -0.004), vignette_ev=-1.6, breathing=0.4, chromatic=0.2, close_focus=0.85),
    LensPreset("T135", "Telephoto 135mm f/2", "SPHERICAL", 135, 135, 2.0, 9,
               "Telephoto prime, slight pincushion",
               dist_wide=_sph(0.004, 0.006), vignette_ev=-1.0, breathing=0.3, chromatic=0.2, close_focus=1.0),
    LensPreset("M100", "Macro 100mm f/2.8", "SPHERICAL", 100, 100, 2.8, 9,
               "Macro lens; strong breathing at close focus",
               dist_wide=_sph(0.069, -0.054), vignette_ev=-0.8, breathing=1.0, chromatic=0.1, close_focus=0.3),
    LensPreset("V58", "Vintage 58mm f/2", "SPHERICAL", 58, 58, 2.0, 8,
               "Older design: heavy vignetting and colour fringing",
               dist_wide=_sph(-0.01, 0.0), vignette_ev=-2.2, breathing=0.6, chromatic=0.8, close_focus=0.5),
    LensPreset("CINE32", "Cinema Prime 32mm T1.5", "SPHERICAL", 32, 32, 1.5, 11,
               "Cine prime with minimal breathing",
               dist_wide=_sph(-0.03, 0.01), vignette_ev=-1.0, breathing=0.05, chromatic=0.1, close_focus=0.35),
    LensPreset("PHONE", "Smartphone Main 6.9mm f/1.8", "SPHERICAL", 6.9, 6.9, 1.8, 0,
               "Phone main camera (distortion already corrected in-camera); use with the Smartphone body",
               dist_wide=_sph(0.0, 0.0), vignette_ev=-0.5, breathing=0.0, chromatic=0.1, close_focus=0.1),
    # --- Spherical zooms ----------------------------------------------------
    LensPreset("Z16_35", "Wide Zoom 16–35mm f/2.8", "SPHERICAL", 16, 35, 2.8, 9,
               "Barrel at 16mm, slight pincushion at 35mm",
               dist_wide=_sph(-0.080, 0.047), dist_tele=_sph(0.024, 0.0),
               vignette_ev=-1.5, breathing=0.2, chromatic=0.3, close_focus=0.28),
    LensPreset("Z24_70", "Standard Zoom 24–70mm f/2.8", "SPHERICAL", 24, 70, 2.8, 9,
               "Barrel at 24mm, pincushion at 70mm",
               dist_wide=_sph(-0.075, 0.044), dist_tele=_sph(0.045, -0.020),
               vignette_ev=-1.3, breathing=0.2, chromatic=0.25, close_focus=0.38),
    LensPreset("Z70_200", "Telephoto Zoom 70–200mm f/2.8", "SPHERICAL", 70, 200, 2.8, 8,
               "Mild barrel at 70mm, mild pincushion at 200mm",
               dist_wide=_sph(-0.007, -0.017), dist_tele=_sph(0.002, 0.008),
               vignette_ev=-1.0, breathing=0.3, chromatic=0.2, close_focus=1.2),
    # --- Anamorphic ---------------------------------------------------------
    LensPreset("ANA2X40", "Anamorphic 2× 40mm T2.3", "ANAMORPHIC", 40, 40, 2.3, 9,
               "Classic 2× scope look for 4:3 sensors: barrel, oval bokeh, streak flares",
               squeeze=2.0, dist_wide=_ana(1.0, 0.065, 0.030, 0.010, -0.005, 0.015, 0.005),
               vignette_ev=-1.5, breathing=0.3, chromatic=0.3, flare=True, close_focus=0.8),
    LensPreset("ANA2X75", "Anamorphic 2× 75mm T2.3", "ANAMORPHIC", 75, 75, 2.3, 9,
               "2× scope medium lens, gentler distortion",
               squeeze=2.0, dist_wide=_ana(0.5, 0.065, 0.030, 0.010, -0.005, 0.015, 0.005),
               vignette_ev=-1.2, breathing=0.3, chromatic=0.25, flare=True, close_focus=1.0),
    LensPreset("ANA133X35", "Anamorphic 1.33× 35mm T2.4", "ANAMORPHIC", 35, 35, 2.4, 9,
               "1.33× for 16:9 sensors (16:9 → 2.39:1)",
               squeeze=1.33, dist_wide=_ana(1.0, 0.040, 0.020, 0.006, 0.0, 0.008, 0.003),
               vignette_ev=-1.3, breathing=0.3, chromatic=0.3, flare=True, close_focus=0.6),
    LensPreset("ANA15X50", "Anamorphic 1.5× 50mm T2.8", "ANAMORPHIC", 50, 50, 2.8, 9,
               "1.5× full-frame anamorphic",
               squeeze=1.5, dist_wide=_ana(1.0, 0.035, 0.018, 0.006, 0.0, 0.006, 0.002),
               vignette_ev=-1.2, breathing=0.3, chromatic=0.25, flare=True, close_focus=0.75),
    LensPreset("ANA18X50", "Anamorphic 1.8× 50mm T2.8", "ANAMORPHIC", 50, 50, 2.8, 9,
               "1.8× full-frame anamorphic",
               squeeze=1.8, dist_wide=_ana(1.0, 0.045, 0.020, 0.008, -0.003, 0.010, 0.003),
               vignette_ev=-1.3, breathing=0.3, chromatic=0.3, flare=True, close_focus=0.75),
    # --- Fisheye ------------------------------------------------------------
    LensPreset("FISH8", "Circular Fisheye 8mm f/3.5", "FISHEYE", 8, 8, 3.5, 6,
               "180° equisolid image circle inside a full-frame sensor",
               dist_wide={"fish_proj": 1, "fish_fov": 180.0}, vignette_ev=-0.7, chromatic=0.4, close_focus=0.2),
    LensPreset("FISH15", "Full-Frame Fisheye 15mm f/2.8", "FISHEYE", 15, 15, 2.8, 7,
               "Diagonal ~180° equisolid fisheye that fills the frame",
               dist_wide={"fish_proj": 1, "fish_fov": 190.0}, vignette_ev=-0.8, chromatic=0.4, close_focus=0.2),
)

LENS_BY_ID = {lens.id: lens for lens in LENSES}


# ---------------------------------------------------------------------------
# Compilation to driver expressions
# ---------------------------------------------------------------------------

def _num(x):
    return repr(round(float(x), 9))


def _zoom_t(lens):
    """Expression for zoom position 0..1, linear in 1/f (distortion changes fastest when wide)."""
    if not lens.is_zoom:
        return None
    inv0, inv1 = 1.0 / lens.focal_min, 1.0 / lens.focal_max
    return f"clamp(({_num(inv0)} - 1 / f) / {_num(inv0 - inv1)}, 0, 1)"


def _blend(lens, key, scale_expr="md"):
    a = lens.dist_wide.get(key, 0.0)
    b = lens.dist_tele.get(key, a) if lens.dist_tele else a
    t = _zoom_t(lens)
    if t is None or a == b:
        if a == 0.0:
            return "0.0"
        return f"{_num(a)} * {scale_expr}"
    return f"lerp({_num(a)}, {_num(b)}, {t}) * {scale_expr}"


def compile_expressions(lens):
    """Return ``(model_id, {param: expression})`` for every non-default parameter.

    Parameters that are not listed keep their schema default.
    """
    exprs = {}
    if lens.kind == "SPHERICAL":
        model = "BROWN"
        exprs["k1"] = _blend(lens, "a", f"pow(f / {_num(FF_HALF_DIAGONAL)}, 2) * md")
        exprs["k2"] = _blend(lens, "b", f"pow(f / {_num(FF_HALF_DIAGONAL)}, 4) * md")
    elif lens.kind == "ANAMORPHIC":
        model = "TDE_ANAMORPHIC"
        for key in ("cx02", "cy02", "cx22", "cy22", "cx04", "cy04"):
            exprs[key] = _blend(lens, key)
    else:
        model = "FISHEYE"
        exprs["fish_proj"] = _num(int(lens.dist_wide.get("fish_proj", 1)))
        exprs["fish_fov"] = _num(lens.dist_wide.get("fish_fov", 180.0))
        for key in ("fish_k1", "fish_k2"):
            exprs[key] = _blend(lens, key)

    if lens.vignette_ev:
        corner = 2.0 ** lens.vignette_ev - 1.0
        # Optical vignetting fades out over ~3 stops of stopping down (2 stops per doubling of N).
        fade = f"clamp(1 - 2 * log(N / {_num(lens.max_aperture)}, 2) / 3, 0, 1)"
        exprs["vig_k1"] = f"{_num(corner)} * {fade} * mv"
    if lens.breathing:
        exprs["breathing"] = f"{_num(lens.breathing)} * mb"
    return model, exprs


_EVAL_NS = {
    "__builtins__": {},
    "clamp": lambda x, lo, hi: min(max(x, lo), hi),
    "lerp": lambda a, b, t: a + (b - a) * t,
    "pow": math.pow,
    "log": math.log,
    "sqrt": math.sqrt,
    "min": min,
    "max": max,
}


def evaluate(expression, f, N, md=1.0, mv=1.0, mb=1.0):
    """Evaluate a compiled expression exactly like Blender's simple-expression driver."""
    ns = dict(_EVAL_NS)
    ns.update(f=float(f), N=float(N), md=float(md), mv=float(mv), mb=float(mb))
    return float(eval(expression, ns))  # noqa: S307 - expressions are generated by this module


def compile_params(lens, focal, fstop, md=1.0, mv=1.0, mb=1.0):
    """Evaluate a preset into ``(model_id, full parameter dict)``."""
    model, exprs = compile_expressions(lens)
    params = schema.default_params()
    for name, expr in exprs.items():
        params[name] = evaluate(expr, focal, fstop, md, mv, mb)
    return model, params
