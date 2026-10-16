"""Single source of truth for lens-model parameters.

Every entry here becomes:

* a Blender property on ``Camera.lensder`` (``props.py``),
* a row in the Advanced UI (``ui.py``),
* a parameter of the generated OSL camera shader (``core/osl.py``),
* a driver connecting the two (``backend_osl.py``).

Coordinate conventions (shared by every model, the shader and the ST-map
baker):

* Film coordinates are millimetres on the *desqueezed* frame, measured from
  the frame centre, with x to the right and y up.
* ``center_x/center_y`` move the distortion centre. The optical axis stays at
  the frame centre, as in 3DEqualizer's "lens centre offset". For fisheye
  projections the centre is also the projection centre.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: float
    soft_min: float
    soft_max: float
    description: str
    is_int: bool = False
    unit: str = ""  # "", "mm", "deg"
    precision: int = 4


@dataclass(frozen=True)
class Model:
    id: str
    index: int  # value passed to the OSL shader
    label: str
    description: str
    params: tuple  # names of model-specific parameters


# Model-independent lens parameters ------------------------------------------

COMMON = (
    Param("center_x", "Center X", 0.0, -2.0, 2.0,
          "Horizontal offset of the distortion centre from the frame centre (mm)", unit="mm"),
    Param("center_y", "Center Y", 0.0, -2.0, 2.0,
          "Vertical offset of the distortion centre from the frame centre (mm)", unit="mm"),
    Param("breathing", "Focus Breathing", 0.0, -1.0, 1.0,
          "Field-of-view change with focus distance. 1 = thin lens with unit focusing "
          "(image narrows at close focus); 0 = no breathing; negative widens"),
    Param("vig_k1", "Vignette r²", 0.0, -1.0, 0.5,
          "Vignetting polynomial V = 1 + v1·ρ² + v2·ρ⁴ + v3·ρ⁶, ρ = 1 at the frame corner"),
    Param("vig_k2", "Vignette r⁴", 0.0, -1.0, 0.5, "Vignetting ρ⁴ coefficient"),
    Param("vig_k3", "Vignette r⁶", 0.0, -1.0, 0.5, "Vignetting ρ⁶ coefficient"),
    Param("cos4", "Natural Falloff", 0.0, 0.0, 1.0,
          "Blend of the physical cos⁴θ illumination falloff (strong on wide lenses)"),
)


# Model-specific parameters ---------------------------------------------------

_BROWN = (
    Param("k1", "K1", 0.0, -0.5, 0.5, "Radial r² coefficient (negative = barrel)"),
    Param("k2", "K2", 0.0, -0.5, 0.5, "Radial r⁴ coefficient"),
    Param("k3", "K3", 0.0, -0.5, 0.5, "Radial r⁶ coefficient"),
    Param("k4", "K4", 0.0, -0.5, 0.5, "Radial r⁸ coefficient"),
    Param("p1", "P1", 0.0, -0.05, 0.05,
          "Tangential coefficient, Blender/libmv order: x += p1·(r²+2x²). "
          "OpenCV's p1/p2 are swapped relative to this"),
    Param("p2", "P2", 0.0, -0.05, 0.05, "Tangential coefficient: y += p2·(r²+2y²)"),
)

_DIVISION = (
    Param("div_k1", "K1", 0.0, -0.5, 0.5, "Division model r² coefficient (positive = barrel)"),
    Param("div_k2", "K2", 0.0, -0.5, 0.5, "Division model r⁴ coefficient"),
)

_NUKE = (
    Param("nuke_k1", "K1", 0.0, -0.5, 0.5,
          "Nuke radial r² coefficient (distorted → undistorted; positive = barrel)"),
    Param("nuke_k2", "K2", 0.0, -0.5, 0.5, "Nuke radial r⁴ coefficient"),
    Param("nuke_p1", "P1", 0.0, -0.1, 0.1, "Nuke asymmetric term (x scale by y²)"),
    Param("nuke_p2", "P2", 0.0, -0.1, 0.1, "Nuke asymmetric term (y scale by x²)"),
)

_TDE_RADIAL = (
    Param("tde_c2", "Distortion (deg 2)", 0.0, -0.5, 0.5, "Radial r² term (positive = barrel)"),
    Param("tde_u1", "U (deg 2)", 0.0, -0.1, 0.1, "Decentering, x, degree 2"),
    Param("tde_v1", "V (deg 2)", 0.0, -0.1, 0.1, "Decentering, y, degree 2"),
    Param("tde_c4", "Quartic Distortion (deg 4)", 0.0, -0.5, 0.5, "Radial r⁴ term"),
    Param("tde_u3", "U (deg 4)", 0.0, -0.1, 0.1, "Decentering, x, degree 4"),
    Param("tde_v3", "V (deg 4)", 0.0, -0.1, 0.1, "Decentering, y, degree 4"),
    Param("tde_phi", "Cylindric Direction", 0.0, -90.0, 90.0,
          "Direction of the cylindric correction (degrees)", unit="deg", precision=2),
    Param("tde_b", "Cylindric Bending", 0.0, -0.1, 0.1, "Amount of cylindric correction"),
)

# (m, n) pairs for the anamorphic terms  C·mn · r^m · cos(nθ)
ANAMORPHIC_TERMS = ((2, 0), (2, 2), (4, 0), (4, 2), (4, 4), (6, 0), (6, 2), (6, 4), (6, 6))


def _anamorphic_params():
    out = []
    for m, n in ANAMORPHIC_TERMS:
        for axis in ("x", "y"):
            name = f"c{axis}{n}{m}"  # 3DE naming: Cx02 = n=0, m=2
            label = f"C{axis}{n}{m}"
            desc = f"{axis.upper()} coefficient of r^{m}·cos({n}θ) (distorted → undistorted)"
            out.append(Param(name, label, 0.0, -0.5, 0.5, desc))
    out += [
        Param("ana_rot", "Lens Rotation", 0.0, -10.0, 10.0,
              "Rotation of the anamorphic cylinder (degrees)", unit="deg", precision=2),
        Param("ana_sqx", "Squeeze-X", 1.0, 0.9, 1.1, "Residual horizontal squeeze correction"),
        Param("ana_sqy", "Squeeze-Y", 1.0, 0.9, 1.1, "Residual vertical squeeze correction"),
        Param("ana_pa", "Pixel Aspect", 1.0, 0.5, 2.0,
              "Pixel aspect used when evaluating the polynomial (3DE convention). "
              "Set to the squeeze when pasting coefficients solved on squeezed footage"),
    ]
    return tuple(out)


_TDE_ANAMORPHIC = _anamorphic_params()

FISHEYE_PROJECTIONS = (
    ("EQUIDISTANT", "Equidistant", "r = f·θ"),
    ("EQUISOLID", "Equisolid Angle", "r = 2f·sin(θ/2) — most photographic fisheyes"),
    ("STEREOGRAPHIC", "Stereographic", "r = 2f·tan(θ/2) — keeps shapes at the edges"),
    ("ORTHOGRAPHIC", "Orthographic", "r = f·sin θ"),
)

_FISHEYE = (
    Param("fish_proj", "Projection", 1, 0, 3, "Fisheye projection", is_int=True),
    Param("fish_k1", "K1", 0.0, -0.5, 0.5, "Kannala–Brandt θ³ coefficient (OpenCV fisheye)"),
    Param("fish_k2", "K2", 0.0, -0.5, 0.5, "Kannala–Brandt θ⁵ coefficient"),
    Param("fish_k3", "K3", 0.0, -0.5, 0.5, "Kannala–Brandt θ⁷ coefficient"),
    Param("fish_k4", "K4", 0.0, -0.5, 0.5, "Kannala–Brandt θ⁹ coefficient"),
    Param("fish_fov", "Image Circle FOV", 180.0, 90.0, 220.0,
          "Rays beyond this full angle are black (the lens' image circle)", unit="deg", precision=1),
)

MODELS = (
    Model("NONE", 0, "None (Ideal)", "Perfect rectilinear lens", ()),
    Model("BROWN", 1, "Brown–Conrady",
          "Radial + tangential polynomial; same convention as Blender's Movie Clip tracker",
          tuple(p.name for p in _BROWN)),
    Model("DIVISION", 2, "Division",
          "Division model; matches Blender's tracker 'Divisions' model",
          tuple(p.name for p in _DIVISION)),
    Model("NUKE", 3, "Nuke",
          "Nuke LensDistortion classic model (as used by Blender's tracker)",
          tuple(p.name for p in _NUKE)),
    Model("TDE_RADIAL", 4, "3DE4 Radial Standard Deg 4",
          "Radial decentered cylindric degree-4 model (3DEqualizer)",
          tuple(p.name for p in _TDE_RADIAL)),
    Model("TDE_ANAMORPHIC", 5, "3DE4 Anamorphic Deg 4/6",
          "Anamorphic Standard degree-4 with rotate/squeeze, extended with degree-6 terms",
          tuple(p.name for p in _TDE_ANAMORPHIC)),
    Model("FISHEYE", 6, "Fisheye",
          "Fisheye projection with Kannala–Brandt polynomial (OpenCV fisheye)",
          tuple(p.name for p in _FISHEYE)),
)

MODEL_BY_ID = {m.id: m for m in MODELS}

ALL_PARAMS = COMMON + _BROWN + _DIVISION + _NUKE + _TDE_RADIAL + _TDE_ANAMORPHIC + _FISHEYE
PARAM_BY_NAME = {p.name: p for p in ALL_PARAMS}

assert len(PARAM_BY_NAME) == len(ALL_PARAMS), "duplicate parameter name"


def default_params():
    """Return ``{name: default}`` for every lens parameter."""
    return {p.name: p.default for p in ALL_PARAMS}
