"""Film-space geometry for the viewport lens overlay (no bpy).

The overlay shows how straight lines in the world bend through the lens: a
grid of straight lines on the ideal (pinhole) image plane is pushed through
the lens model to distorted film positions.
"""

import math

import numpy as np

from . import lens as L


def grid_polylines(state, lines=8, samples=48):
    """Return polylines (lists of (x_mm, y_mm)) on the distorted film, clipped to the frame."""
    w, h = state.frame_w, state.frame_h
    if state.model == "FISHEYE":
        return clip_to_frame(_fisheye_grid(state, lines, samples), w, h)
    return clip_to_frame(_rectilinear_grid(state, lines, samples), w, h)


def clip_to_frame(polylines, w, h, margin=1e-6):
    """Split polylines where they leave the frame rectangle."""
    hx, hy = 0.5 * w + margin, 0.5 * h + margin
    out = []
    for line in polylines:
        cur = []
        for x, y in line:
            if abs(x) <= hx and abs(y) <= hy:
                cur.append((x, y))
            else:
                if len(cur) > 1:
                    out.append(cur)
                cur = []
        if len(cur) > 1:
            out.append(cur)
    return out


def _rectilinear_grid(state, lines, samples):
    w, h = state.frame_w, state.frame_h

    # Extent of the undistorted image plane seen through the frame.
    t = np.linspace(-0.5, 0.5, 33)
    bx = np.concatenate([t * w, t * w, np.full_like(t, -0.5 * w), np.full_like(t, 0.5 * w)])
    by = np.concatenate([np.full_like(t, -0.5 * h), np.full_like(t, 0.5 * h), t * h, t * h])
    dx, dy, dz, ok = L.film_to_ray(state, bx, by)
    f = state.focal_eff
    if ok.any():
        ux = np.abs(dx[ok] / dz[ok] * f).max()
        uy = np.abs(dy[ok] / dz[ok] * f).max()
    else:
        ux, uy = 0.5 * w, 0.5 * h
    polylines = []
    s = np.linspace(-1.0, 1.0, samples)
    for i in range(lines + 1):
        a = -1.0 + 2.0 * i / lines
        # vertical line x = a·ux, horizontal line y = a·uy (ideal plane, mm)
        for xs, ys in ((np.full_like(s, a * ux), s * uy), (s * ux, np.full_like(s, a * uy))):
            px, py, good = L.ray_to_film(state, xs / f, ys / f, np.ones_like(xs))
            polylines.extend(_split(px, py, good))
    return polylines


def _fisheye_grid(state, lines, samples):
    """Lines of constant azimuth/elevation (like a globe) up to the image circle."""
    half = math.radians(state.p("fish_fov")) * 0.5
    polylines = []
    s = np.linspace(-half, half, samples)
    for i in range(lines + 1):
        a = -half + 2.0 * half * i / lines
        for yaw, pitch in ((np.full_like(s, a), s), (s, np.full_like(s, a))):
            dx = np.sin(yaw) * np.cos(pitch)
            dy = np.sin(pitch)
            dz = np.cos(yaw) * np.cos(pitch)
            px, py, good = L.ray_to_film(state, dx, dy, dz)
            polylines.extend(_split(px, py, good))
    # Image circle
    phi = np.linspace(0, 2 * math.pi, 96)
    theta = np.full_like(phi, half)
    px, py, good = L.ray_to_film(state, np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta))
    polylines.extend(_split(px, py, good))
    return polylines


def _split(px, py, good):
    out, cur = [], []
    for x, y, g in zip(px, py, good):
        if g and math.isfinite(x) and math.isfinite(y):
            cur.append((float(x), float(y)))
        elif len(cur) > 1:
            out.append(cur)
            cur = []
        else:
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return out


def frame_outline(state):
    w, h = state.frame_w * 0.5, state.frame_h * 0.5
    return [[(-w, -h), (w, -h), (w, h), (-w, h), (-w, -h)]]
