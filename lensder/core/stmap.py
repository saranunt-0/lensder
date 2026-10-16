"""ST-map baking for the post-process (compositor) distortion path.

The scene is rendered with an ordinary pinhole camera that has the nominal
focal length and a frame enlarged by ``overscan``. For each pixel of the final
(distorted) image, the ST-map stores where to sample that pinhole render:

* R, G: UV in [0, 1], origin bottom-left (Blender Map UV convention)
* B:    coverage (1 = valid ray, 0 = outside the lens model / image circle)
* A:    vignetting factor
"""

import numpy as np

from . import lens as L

MAX_OVERSCAN = 3.0


def _pixel_grid(width, height, frame_w, frame_h):
    xs = ((np.arange(width) + 0.5) / width - 0.5) * frame_w
    ys = ((np.arange(height) + 0.5) / height - 0.5) * frame_h
    return np.meshgrid(xs, ys)


def required_overscan(state, samples=64, margin=1.01):
    """Pinhole frame enlargement needed so that every distorted pixel is covered.

    Values below 1 (pincushion) are kept. Rendering a slightly narrower field of
    view then gives the centre more resolution.
    """
    w, h = state.frame_w, state.frame_h
    t = np.linspace(-0.5, 0.5, samples)
    edge_x = np.concatenate([t * w, t * w, np.full_like(t, -0.5 * w), np.full_like(t, 0.5 * w)])
    edge_y = np.concatenate([np.full_like(t, -0.5 * h), np.full_like(t, 0.5 * h), t * h, t * h])
    # The interior can also exceed the border (mustache), so add a coarse grid.
    gx, gy = _pixel_grid(16, 16, w, h)
    px = np.concatenate([edge_x, gx.ravel()])
    py = np.concatenate([edge_y, gy.ravel()])
    dx, dy, dz, ok = L.film_to_ray(state, px, py)
    ok &= dz > 1e-6
    if not ok.any():
        return 1.0
    ux = np.abs(state.focal * dx[ok] / dz[ok]) / (0.5 * w)
    uy = np.abs(state.focal * dy[ok] / dz[ok]) / (0.5 * h)
    need = float(max(ux.max(), uy.max())) * margin
    return min(max(need, 0.25), MAX_OVERSCAN)


def _exact(state, gx, gy, overscan):
    dx, dy, dz, ok = L.film_to_ray(state, gx, gy)
    front = ok & (dz > 1e-6)
    safe = np.where(front, dz, 1.0)
    u = 0.5 + (state.focal * dx / safe) / (state.frame_w * overscan)
    v = 0.5 + (state.focal * dy / safe) / (state.frame_h * overscan)
    vig = L.vignette(state, gx, gy, dz)
    return u, v, front.astype(np.float64), vig


def _lerp_index(coords, lattice):
    f = (coords - lattice[0]) / (lattice[1] - lattice[0])
    i = np.clip(np.floor(f).astype(np.int64), 0, lattice.size - 2)
    return i, f - i


def _upsample(a, xs_c, ys_c, xs, ys):
    """Separable bilinear interpolation from a regular coarse lattice. The lattice
    extends past the image border, so there is no edge clamping."""
    ix, tx = _lerp_index(xs, xs_c)
    iy, ty = _lerp_index(ys, ys_c)
    rows = a[:, ix] * (1 - tx) + a[:, ix + 1] * tx  # (coarse_h, width)
    ty = ty[:, None]
    return rows[iy] * (1 - ty) + rows[iy + 1] * ty


def bake(state, width, height, overscan, step=4):
    """Return a float32 array (height, width, 4) ready for ``Image.pixels``.

    The map is evaluated exactly on a lattice every ``step`` pixels (extending
    one cell past each border) and interpolated in between; it is smooth, so the
    error is second order (well below 0.01 px for real lenses). ``step=1``
    evaluates every pixel.
    """
    xs = ((np.arange(width) + 0.5) / width - 0.5) * state.frame_w
    ys = ((np.arange(height) + 0.5) / height - 0.5) * state.frame_h
    if state.model == "FISHEYE":
        # The pinhole intermediate is far too non-linear near 90° to interpolate;
        # the fisheye model is direct (no Newton), so exact evaluation is cheap.
        step = 1
    if step <= 1:
        gx, gy = np.meshgrid(xs, ys)
        u, v, front, vig = _exact(state, gx, gy, overscan)
    else:
        px_w = state.frame_w / width
        px_h = state.frame_h / height
        xs_c = np.arange(xs[0] - step * px_w, xs[-1] + step * px_w * 1.5, step * px_w)
        ys_c = np.arange(ys[0] - step * px_h, ys[-1] + step * px_h * 1.5, step * px_h)
        cgx, cgy = np.meshgrid(xs_c, ys_c)
        cu, cv, cfront, cvig = _exact(state, cgx, cgy, overscan)
        # Interpolation is only trusted where every lattice sample is a valid ray
        # that lands near the pinhole frame. Elsewhere (image-circle edges, rays
        # near 90° where the pinhole coordinate blows up) evaluate pixels exactly.
        csafe = ((cfront > 0.5) & (np.abs(cu - 0.5) < 0.75) & (np.abs(cv - 0.5) < 0.75)).astype(np.float64)
        cu = np.where(csafe > 0, cu, 0.5)
        cv = np.where(csafe > 0, cv, 0.5)
        u = _upsample(cu, xs_c, ys_c, xs, ys)
        v = _upsample(cv, xs_c, ys_c, xs, ys)
        vig = _upsample(cvig, xs_c, ys_c, xs, ys)
        front = np.ones_like(u)
        unsafe = _upsample(csafe, xs_c, ys_c, xs, ys) < 1.0
        if unsafe.any():
            gx, gy = np.meshgrid(xs, ys)
            eu, ev, ef, evig = _exact(state, gx[unsafe], gy[unsafe], overscan)
            u[unsafe], v[unsafe], front[unsafe], vig[unsafe] = eu, ev, ef, evig
    front = front >= 0.5
    inside = front & (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (v <= 1.0)
    out = np.empty((height, width, 4), dtype=np.float32)
    out[..., 0] = np.where(inside, u, 0.0)
    out[..., 1] = np.where(inside, v, 0.0)
    out[..., 2] = inside.astype(np.float32)
    out[..., 3] = np.where(inside, vig, 0.0)
    return out
