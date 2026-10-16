"""Vectorised lens model: film position <-> ray direction.

The functions here are the reference implementation. The OSL shader generated
in ``core/osl.py`` implements the same steps, and the tests check that the
two agree.

Pipeline for one film point ``p`` (mm, desqueezed frame, centred):

1. ``q = (p - c) / N``         normalise (N depends on the model)
2. ``q_u = undistort(q)``      model-specific; may be a Newton inverse
3. ``p_u = c + q_u * N``       back to undistorted film millimetres
4. ``ray = (p_u / f_eff, 1)``  ``f_eff`` includes focus breathing

Fisheye models replace steps 2–4 with an angular projection.
"""

from dataclasses import dataclass, field
import math

import numpy as np

from . import schema


@dataclass
class LensState:
    """Everything needed to evaluate the camera mapping."""

    model: str = "NONE"
    focal: float = 50.0  # mm
    frame_w: float = 36.0  # mm, desqueezed frame width (Cycles cam:sensor_size.x)
    frame_h: float = 24.0  # mm
    focus_distance: float = 0.0  # scene units (0 = infinity / DOF off)
    params: dict = field(default_factory=schema.default_params)

    def p(self, name):
        return self.params.get(name, schema.PARAM_BY_NAME[name].default)

    @property
    def half_diagonal(self):
        return 0.5 * math.hypot(self.frame_w, self.frame_h)

    @property
    def focal_eff(self):
        return breathing_focal(self.focal, self.focus_distance, self.p("breathing"))


def breathing_focal(focal_mm, focus_distance, amount):
    """Effective focal length after focus breathing.

    Thin lens: the image distance is v = f·d/(d-f), so the field of view
    narrows by f/(d-f). ``amount`` scales that (1 = unit focusing).
    """
    if amount == 0.0 or focus_distance <= 0.0:
        return focal_mm
    d_mm = focus_distance * 1000.0
    denom = max(d_mm - focal_mm, focal_mm * 0.05)
    return focal_mm * (1.0 + amount * focal_mm / denom)


# ---------------------------------------------------------------------------
# Model primitives (normalised coordinates)
# ---------------------------------------------------------------------------

def brown_distort(x, y, k1, k2, k3, k4, p1, p2):
    """Undistorted -> distorted (Blender/libmv Brown convention)."""
    x2, y2 = x * x, y * y
    r2 = x2 + y2
    xy2 = 2.0 * x * y
    rc = 1.0 + r2 * (k1 + r2 * (k2 + r2 * (k3 + r2 * k4)))
    xd = x * rc + p1 * (r2 + 2.0 * x2) + p2 * xy2
    yd = y * rc + p2 * (r2 + 2.0 * y2) + p1 * xy2
    return xd, yd


def division_distort(x, y, k1, k2):
    """Undistorted -> distorted (Blender/libmv division convention)."""
    r2 = x * x + y * y
    s = 1.0 / (1.0 + k1 * r2 + k2 * r2 * r2)
    return x * s, y * s


def nuke_undistort(x, y, k1, k2, p1, p2):
    """Distorted -> undistorted (Nuke classic, as in libmv)."""
    x2, y2 = x * x, y * y
    rd2 = x2 + y2
    rad = 1.0 + k1 * rd2 + k2 * rd2 * rd2
    return x / (rad + p1 * y2), y / (rad + p2 * x2)


def tde_radial_undistort(x, y, c2, u1, v1, c4, u3, v3, phi_deg, b):
    """Distorted -> undistorted (3DE4 Radial Standard Degree 4)."""
    x2, y2 = x * x, y * y
    xy = x * y
    r2 = x2 + y2
    r4 = r2 * r2
    radial = 1.0 + c2 * r2 + c4 * r4
    xu = x * radial + (r2 + 2.0 * x2) * (u1 + u3 * r2) + 2.0 * xy * (v1 + v3 * r2)
    yu = y * radial + (r2 + 2.0 * y2) * (v1 + v3 * r2) + 2.0 * xy * (u1 + u3 * r2)
    # Cylindric correction (symmetric 2x2 matrix).
    q = math.sqrt(max(1.0 + b, 1e-9))
    c = math.cos(math.radians(phi_deg))
    s = math.sin(math.radians(phi_deg))
    mxx = c * c * q + s * s / q
    mxy = (q - 1.0 / q) * c * s
    myy = c * c / q + s * s * q
    return mxx * xu + mxy * yu, mxy * xu + myy * yu


def _re_z_pow(x, y, n):
    """Real part of (x + i·y)^n, i.e. r^n·cos(nθ), without trigonometry."""
    re, im = np.ones_like(x), np.zeros_like(x)
    for _ in range(n):
        re, im = re * x - im * y, re * y + im * x
    return re


def tde_anamorphic_undistort(x, y, params):
    """Distorted -> undistorted (3DE4 Anamorphic, rotate-squeeze, degree 6).

    Matches Natron's DistortionModel3DEAnamorphic4/6 and AliceVision's
    Undistortion3DEAnamorphic4 (see docs/research.md).
    """
    pa = params["ana_pa"]
    phi = math.radians(params["ana_rot"])
    cphi, sphi = math.cos(phi), math.sin(phi)
    x = x / pa
    xr = cphi * x + sphi * y
    yr = -sphi * x + cphi * y
    r2 = xr * xr + yr * yr
    fx = np.ones_like(xr)
    fy = np.ones_like(xr)
    for m, n in schema.ANAMORPHIC_TERMS:
        cx = params[f"cx{n}{m}"]
        cy = params[f"cy{n}{m}"]
        if cx == 0.0 and cy == 0.0:
            continue
        # r^m cos(nθ) = r^(m-n) · Re(z^n);  m-n is even.
        term = r2 ** ((m - n) // 2) * _re_z_pow(xr, yr, n)
        fx = fx + cx * term
        fy = fy + cy * term
    xq = xr * fx * pa * params["ana_sqx"]
    yq = yr * fy * params["ana_sqy"]
    return cphi * xq - sphi * yq, sphi * xq + cphi * yq


# ---------------------------------------------------------------------------
# Generic 2D Newton inverse
# ---------------------------------------------------------------------------

def newton_inverse(func, tx, ty, iterations=30, tol=1e-10, eps=1e-6):
    """Solve ``func(x, y) == (tx, ty)`` element-wise.

    Uses a central-difference Jacobian, so it works for every model. Returns
    ``(x, y, converged_mask)``.
    """
    shape = np.shape(tx)
    tx = np.asarray(tx, dtype=np.float64).ravel()
    ty = np.asarray(ty, dtype=np.float64).ravel()
    x = tx.copy()
    y = ty.copy()
    active = np.arange(tx.size)  # only iterate points that have not converged
    for _ in range(iterations):
        if active.size == 0:
            break
        xa, ya = x[active], y[active]
        fx, fy = func(xa, ya)
        ex, ey = fx - tx[active], fy - ty[active]
        moving = ~((np.abs(ex) < tol) & (np.abs(ey) < tol))
        active, xa, ya, ex, ey = active[moving], xa[moving], ya[moving], ex[moving], ey[moving]
        if active.size == 0:
            break
        ax, ay = func(xa + eps, ya)
        bx, by = func(xa - eps, ya)
        cx, cy = func(xa, ya + eps)
        dx, dy = func(xa, ya - eps)
        j11 = (ax - bx) / (2 * eps)
        j21 = (ay - by) / (2 * eps)
        j12 = (cx - dx) / (2 * eps)
        j22 = (cy - dy) / (2 * eps)
        det = j11 * j22 - j12 * j21
        good = np.abs(det) >= 1e-12
        active, xa, ya, ex, ey = active[good], xa[good], ya[good], ex[good], ey[good]
        j11, j12, j21, j22, det = j11[good], j12[good], j21[good], j22[good], det[good]
        x[active] = xa - (j22 * ex - j12 * ey) / det
        y[active] = ya - (-j21 * ex + j11 * ey) / det
    x = x.reshape(shape)
    y = y.reshape(shape)
    tx = tx.reshape(shape)
    ty = ty.reshape(shape)
    fx, fy = func(x, y)
    ok = (np.abs(fx - tx) < 1e-6) & (np.abs(fy - ty) < 1e-6) & np.isfinite(x) & np.isfinite(y)
    # Reject solutions beyond the fold of the lens map: a valid inverse lies on
    # the same side of the centre and where the map is orientation-preserving.
    ax, ay = func(x + eps, y)
    bx, by = func(x - eps, y)
    cx, cy = func(x, y + eps)
    dx, dy = func(x, y - eps)
    det = (ax - bx) * (cy - dy) - (cx - dx) * (ay - by)
    ok &= det > 0.0
    ok &= (x * tx + y * ty) >= 0.0
    return x, y, ok


# ---------------------------------------------------------------------------
# Model dispatch
# ---------------------------------------------------------------------------

def normaliser(state):
    """Length (mm) that maps film mm to the model's normalised coordinates."""
    m = state.model
    if m in ("BROWN", "DIVISION"):
        return state.focal
    if m == "NUKE":
        return 0.5 * max(state.frame_w, state.frame_h)
    if m in ("TDE_RADIAL", "TDE_ANAMORPHIC"):
        return state.half_diagonal
    return 1.0


def _forward_fn(state):
    """Normalised undistorted -> distorted function for perspective models."""
    P = state.params
    m = state.model
    if m == "BROWN":
        return lambda x, y: brown_distort(x, y, P["k1"], P["k2"], P["k3"], P["k4"], P["p1"], P["p2"])
    if m == "DIVISION":
        return lambda x, y: division_distort(x, y, P["div_k1"], P["div_k2"])
    return None


def _undistort_fn(state):
    """Normalised distorted -> undistorted function (direct models only)."""
    P = state.params
    m = state.model
    if m == "NUKE":
        return lambda x, y: nuke_undistort(x, y, P["nuke_k1"], P["nuke_k2"], P["nuke_p1"], P["nuke_p2"])
    if m == "TDE_RADIAL":
        return lambda x, y: tde_radial_undistort(
            x, y, P["tde_c2"], P["tde_u1"], P["tde_v1"], P["tde_c4"],
            P["tde_u3"], P["tde_v3"], P["tde_phi"], P["tde_b"])
    if m == "TDE_ANAMORPHIC":
        return lambda x, y: tde_anamorphic_undistort(x, y, P)
    return None


def undistort_normalised(state, qx, qy):
    """Distorted -> undistorted in the model's normalised space."""
    direct = _undistort_fn(state)
    if direct is not None:
        ux, uy = direct(qx, qy)
        return ux, uy, np.isfinite(ux) & np.isfinite(uy)
    fwd = _forward_fn(state)
    if fwd is not None:
        return newton_inverse(fwd, qx, qy)
    return qx, qy, np.ones(np.shape(qx), dtype=bool)


def distort_normalised(state, qx, qy):
    """Undistorted -> distorted in the model's normalised space."""
    fwd = _forward_fn(state)
    if fwd is not None:
        dx, dy = fwd(qx, qy)
        return dx, dy, np.isfinite(dx) & np.isfinite(dy)
    inv = _undistort_fn(state)
    if inv is not None:
        return newton_inverse(inv, qx, qy)
    return qx, qy, np.ones(np.shape(qx), dtype=bool)


# Fisheye projections: r/f as a function of θ, and the inverse.

def fisheye_r(proj, theta):
    if proj == 0:
        return theta
    if proj == 1:
        return 2.0 * np.sin(theta * 0.5)
    if proj == 2:
        return 2.0 * np.tan(theta * 0.5)
    return np.sin(theta)


def fisheye_theta(proj, r):
    """Inverse projection; returns (theta, valid)."""
    r = np.asarray(r, dtype=np.float64)
    if proj == 0:
        return r, np.ones(r.shape, dtype=bool)
    if proj == 1:
        a = r * 0.5
        ok = a <= 1.0
        return 2.0 * np.arcsin(np.clip(a, -1, 1)), ok
    if proj == 2:
        return 2.0 * np.arctan(r * 0.5), np.ones(r.shape, dtype=bool)
    ok = r <= 1.0
    return np.arcsin(np.clip(r, -1, 1)), ok


def kb_theta_d(theta, P):
    t2 = theta * theta
    return theta * (1.0 + t2 * (P["fish_k1"] + t2 * (P["fish_k2"] + t2 * (P["fish_k3"] + t2 * P["fish_k4"]))))


def kb_inverse(theta_d, P, iterations=20):
    """Solve kb_theta_d(θ) = θd for θ (1D Newton)."""
    th = np.array(theta_d, dtype=np.float64, copy=True)
    if P["fish_k1"] == P["fish_k2"] == P["fish_k3"] == P["fish_k4"] == 0.0:
        return th, np.ones(th.shape, dtype=bool)
    for _ in range(iterations):
        t2 = th * th
        f = kb_theta_d(th, P) - theta_d
        df = 1.0 + t2 * (3 * P["fish_k1"] + t2 * (5 * P["fish_k2"] + t2 * (7 * P["fish_k3"] + t2 * 9 * P["fish_k4"])))
        df = np.where(np.abs(df) < 1e-9, 1e-9, df)
        th = th - f / df
    ok = np.abs(kb_theta_d(th, P) - theta_d) < 1e-6
    return th, ok


# ---------------------------------------------------------------------------
# Camera mapping
# ---------------------------------------------------------------------------

def film_to_ray(state, px, py):
    """Distorted film point (mm) -> unit ray direction in camera space.

    Camera space: +z forward, +y up. Returns ``(dx, dy, dz, valid)``.
    """
    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    cx, cy = state.p("center_x"), state.p("center_y")
    f_eff = state.focal_eff
    if state.model == "FISHEYE":
        P = state.params
        proj = int(P["fish_proj"])
        rx, ry = px - cx, py - cy
        r = np.hypot(rx, ry) / f_eff
        theta_d, ok1 = fisheye_theta(proj, r)
        theta, ok2 = kb_inverse(theta_d, P)
        ok = ok1 & ok2 & (theta <= math.radians(P["fish_fov"]) * 0.5)
        phi = np.arctan2(ry, rx)
        st = np.sin(theta)
        return st * np.cos(phi), st * np.sin(phi), np.cos(theta), ok

    n = normaliser(state)
    qx, qy = (px - cx) / n, (py - cy) / n
    ux, uy, ok = undistort_normalised(state, qx, qy)
    xu = cx + ux * n
    yu = cy + uy * n
    dx, dy, dz = xu / f_eff, yu / f_eff, np.ones_like(xu)
    inv = 1.0 / np.sqrt(dx * dx + dy * dy + 1.0)
    return dx * inv, dy * inv, dz * inv, ok


def ray_to_film(state, dx, dy, dz):
    """Ray direction -> distorted film point (mm). Returns ``(px, py, valid)``."""
    dx = np.asarray(dx, dtype=np.float64)
    dy = np.asarray(dy, dtype=np.float64)
    dz = np.asarray(dz, dtype=np.float64)
    cx, cy = state.p("center_x"), state.p("center_y")
    f_eff = state.focal_eff
    if state.model == "FISHEYE":
        P = state.params
        proj = int(P["fish_proj"])
        norm = np.sqrt(dx * dx + dy * dy + dz * dz)
        theta = np.arccos(np.clip(dz / norm, -1.0, 1.0))
        ok = theta <= math.radians(P["fish_fov"]) * 0.5
        if proj == 3:
            ok &= theta <= math.pi / 2
        r = fisheye_r(proj, kb_theta_d(theta, P)) * f_eff
        phi = np.arctan2(dy, dx)
        return cx + r * np.cos(phi), cy + r * np.sin(phi), ok

    front = dz > 1e-9
    safe_dz = np.where(front, dz, 1.0)
    xu = dx / safe_dz * f_eff
    yu = dy / safe_dz * f_eff
    n = normaliser(state)
    qx, qy = (xu - cx) / n, (yu - cy) / n
    ddx, ddy, ok = distort_normalised(state, qx, qy)
    return cx + ddx * n, cy + ddy * n, ok & front


def vignette(state, px, py, dz):
    """Relative illumination at distorted film point (mm) for ray z-component ``dz``."""
    P = state.params
    rho2 = ((np.asarray(px) - P["center_x"]) ** 2 + (np.asarray(py) - P["center_y"]) ** 2) / state.half_diagonal ** 2
    v = 1.0 + rho2 * (P["vig_k1"] + rho2 * (P["vig_k2"] + rho2 * P["vig_k3"]))
    v = np.maximum(v, 0.0)
    if P["cos4"] > 0.0:
        c4 = np.clip(dz, 0.0, 1.0) ** 4
        v = v * (1.0 - P["cos4"] + P["cos4"] * c4)
    return v


def corner_distortion_percent(state):
    """Distortion at the frame corner in percent of the ideal image height.

    Negative = barrel, positive = pincushion. This is the TV/SMIA-style number
    photographers quote for lenses. Fisheye models report None.
    """
    if state.model in ("NONE", "FISHEYE"):
        return 0.0 if state.model == "NONE" else None
    hx, hy = state.frame_w * 0.5, state.frame_h * 0.5
    dx, dy, dz, ok = film_to_ray(state, np.array([hx]), np.array([hy]))
    if not ok[0]:
        return None
    ideal = math.hypot(dx[0] / dz[0], dy[0] / dz[0]) * state.focal_eff
    actual = math.hypot(hx, hy)
    return (actual / ideal - 1.0) * 100.0
