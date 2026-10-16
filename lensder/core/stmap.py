"""ST-map baking for the post-process (compositor) distortion path.

The scene is rendered with an ordinary pinhole camera that has the nominal
focal length and a frame enlarged by ``overscan``. For each pixel of the final
(distorted) image, the ST-map stores where to sample that pinhole render:

* R, G: UV in [0, 1], origin bottom-left (Blender Map UV convention)
* B:    coverage (1 = valid ray, 0 = outside the lens model / image circle)
* A:    vignetting factor
"""

import math

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


def bake(state, width, height, overscan):
    """Return a float32 array (height, width, 4) ready for ``Image.pixels``."""
    gx, gy = _pixel_grid(width, height, state.frame_w, state.frame_h)
    dx, dy, dz, ok = L.film_to_ray(state, gx, gy)
    front = ok & (dz > 1e-6)
    safe = np.where(front, dz, 1.0)
    u = 0.5 + (state.focal * dx / safe) / (state.frame_w * overscan)
    v = 0.5 + (state.focal * dy / safe) / (state.frame_h * overscan)
    inside = front & (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (v <= 1.0)
    vig = L.vignette(state, gx, gy, dz)
    out = np.empty((height, width, 4), dtype=np.float32)
    out[..., 0] = np.where(inside, u, 0.0)
    out[..., 1] = np.where(inside, v, 0.0)
    out[..., 2] = inside.astype(np.float32)
    out[..., 3] = np.where(inside, vig, 0.0)
    return out


def bake_size(render_w, render_h, max_size=640):
    """ST-maps are smooth, so a reduced size is upscaled bilinearly in the compositor."""
    scale = min(1.0, max_size / max(render_w, render_h))
    return max(2, int(math.ceil(render_w * scale))), max(2, int(math.ceil(render_h * scale)))
