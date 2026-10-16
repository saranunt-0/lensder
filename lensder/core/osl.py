"""Generates the Cycles OSL camera shader.

The parameter list is generated from ``schema.py``. The body implements the
same pipeline as ``lens.film_to_ray`` and ``lens.vignette``; the integration
tests render with it and compare against the NumPy reference.
"""

import hashlib

from . import schema

SHADER_NAME = "lensder_camera"

_BODY = r"""
#define NEWTON_EPS 1e-3

/* Normalised undistorted -> distorted, for models defined in that direction. */
#define FORWARD(X, Y, OX, OY) \
  if (model == 1) { \
    float _x2 = (X) * (X), _y2 = (Y) * (Y), _r2 = _x2 + _y2, _xy2 = 2.0 * (X) * (Y); \
    float _rc = 1.0 + _r2 * (k1 + _r2 * (k2 + _r2 * (k3 + _r2 * k4))); \
    OX = (X) * _rc + p1 * (_r2 + 2.0 * _x2) + p2 * _xy2; \
    OY = (Y) * _rc + p2 * (_r2 + 2.0 * _y2) + p1 * _xy2; \
  } else { \
    float _r2 = (X) * (X) + (Y) * (Y); \
    float _s = 1.0 / (1.0 + div_k1 * _r2 + div_k2 * _r2 * _r2); \
    OX = (X) * _s; OY = (Y) * _s; \
  }

  vector sensor = vector(36.0, 24.0, 0.0);
  getattribute("cam:sensor_size", sensor);
  float W = sensor[0];
  float H = sensor[1];
  float half_diag = 0.5 * hypot(W, H);
  point raster = camera_shader_raster_position();
  float px = (raster[0] - 0.5) * W;
  float py = (raster[1] - 0.5) * H;

  float focus_dist = 0.0;
  getattribute("cam:focal_distance", focus_dist);
  float aperture = 0.0;
  getattribute("cam:aperture_size", aperture);
  /* With depth of field off, Cycles reports a tiny (1e-5) focus distance, not 0:
   * treat "DOF off" as focused at infinity, like the Python reference. */
  int dof_on = aperture > 0.0 && focus_dist > 1e-3;
  float f_eff = focal_length;
  if (breathing != 0.0 && dof_on) {
    float d_mm = focus_dist * 1000.0;
    f_eff = focal_length * (1.0 + breathing * focal_length / max(d_mm - focal_length, focal_length * 0.05));
  }

  vector dir = vector(0.0, 0.0, 1.0);
  int ok = 1;

  if (model == 6) {
    /* Fisheye: angular projection + Kannala-Brandt polynomial. */
    float rx = px - center_x;
    float ry = py - center_y;
    float rn = hypot(rx, ry) / f_eff;
    float theta_d = rn;
    if (fish_proj == 1) {
      if (rn * 0.5 > 1.0) ok = 0; else theta_d = 2.0 * asin(rn * 0.5);
    }
    else if (fish_proj == 2) {
      theta_d = 2.0 * atan(rn * 0.5);
    }
    else if (fish_proj == 3) {
      if (rn > 1.0) ok = 0; else theta_d = asin(rn);
    }
    float th = theta_d;
    if (fish_k1 != 0.0 || fish_k2 != 0.0 || fish_k3 != 0.0 || fish_k4 != 0.0) {
      for (int i = 0; i < 20; i++) {
        float t2 = th * th;
        float fv = th * (1.0 + t2 * (fish_k1 + t2 * (fish_k2 + t2 * (fish_k3 + t2 * fish_k4)))) - theta_d;
        float df = 1.0 + t2 * (3.0 * fish_k1 + t2 * (5.0 * fish_k2 + t2 * (7.0 * fish_k3 + t2 * 9.0 * fish_k4)));
        if (abs(df) < 1e-9) df = 1e-9;
        th -= fv / df;
      }
    }
    if (th > radians(fish_fov) * 0.5 || th < 0.0) ok = 0;
    float phi = atan2(ry, rx);
    dir = vector(sin(th) * cos(phi), sin(th) * sin(phi), cos(th));
  }
  else {
    float n = 1.0;
    if (model == 1 || model == 2) n = focal_length;
    else if (model == 3) n = 0.5 * max(W, H);
    else if (model == 4 || model == 5) n = half_diag;

    float qx = (px - center_x) / n;
    float qy = (py - center_y) / n;
    float ux = qx;
    float uy = qy;

    if (model == 1 || model == 2) {
      /* Newton inverse with a central-difference Jacobian. */
      int conv = 0;
      for (int i = 0; i < 30; i++) {
        float fx, fy;
        FORWARD(ux, uy, fx, fy)
        float ex = fx - qx;
        float ey = fy - qy;
        float ax, ay, bx, by, cxx, cyy, dxx, dyy;
        FORWARD(ux + NEWTON_EPS, uy, ax, ay)
        FORWARD(ux - NEWTON_EPS, uy, bx, by)
        FORWARD(ux, uy + NEWTON_EPS, cxx, cyy)
        FORWARD(ux, uy - NEWTON_EPS, dxx, dyy)
        float j11 = (ax - bx) / (2.0 * NEWTON_EPS);
        float j21 = (ay - by) / (2.0 * NEWTON_EPS);
        float j12 = (cxx - dxx) / (2.0 * NEWTON_EPS);
        float j22 = (cyy - dyy) / (2.0 * NEWTON_EPS);
        float det = j11 * j22 - j12 * j21;
        if (abs(ex) < 1e-6 && abs(ey) < 1e-6) {
          /* Converged; reject solutions beyond the fold of the lens map. */
          conv = (det > 0.0 && ux * qx + uy * qy >= 0.0) ? 1 : 0;
          break;
        }
        if (abs(det) < 1e-12) break;
        ux -= (j22 * ex - j12 * ey) / det;
        uy -= (-j21 * ex + j11 * ey) / det;
      }
      if (!conv) ok = 0;
    }
    else if (model == 3) {
      float x2 = qx * qx, y2 = qy * qy, rd2 = x2 + y2;
      float rad = 1.0 + nuke_k1 * rd2 + nuke_k2 * rd2 * rd2;
      ux = qx / (rad + nuke_p1 * y2);
      uy = qy / (rad + nuke_p2 * x2);
    }
    else if (model == 4) {
      float x2 = qx * qx, y2 = qy * qy, xy = qx * qy, r2 = x2 + y2, r4 = r2 * r2;
      float radial = 1.0 + tde_c2 * r2 + tde_c4 * r4;
      float xr = qx * radial + (r2 + 2.0 * x2) * (tde_u1 + tde_u3 * r2) + 2.0 * xy * (tde_v1 + tde_v3 * r2);
      float yr = qy * radial + (r2 + 2.0 * y2) * (tde_v1 + tde_v3 * r2) + 2.0 * xy * (tde_u1 + tde_u3 * r2);
      float q = sqrt(max(1.0 + tde_b, 1e-9));
      float c = cos(radians(tde_phi));
      float s = sin(radians(tde_phi));
      float mxx = c * c * q + s * s / q;
      float mxy = (q - 1.0 / q) * c * s;
      float myy = c * c / q + s * s * q;
      ux = mxx * xr + mxy * yr;
      uy = mxy * xr + myy * yr;
    }
    else if (model == 5) {
      float cphi = cos(radians(ana_rot));
      float sphi = sin(radians(ana_rot));
      float x0 = qx / ana_pa;
      float xr = cphi * x0 + sphi * qy;
      float yr = -sphi * x0 + cphi * qy;
      float r2 = xr * xr + yr * yr;
      /* Re((x+iy)^n) for n = 2, 4, 6 */
      float re2 = xr * xr - yr * yr;
      float im2 = 2.0 * xr * yr;
      float re4 = re2 * re2 - im2 * im2;
      float im4 = 2.0 * re2 * im2;
      float re6 = re4 * re2 - im4 * im2;
      float r4 = r2 * r2;
      float r6 = r4 * r2;
      float fx = 1.0 + cx02 * r2 + cx22 * re2 + cx04 * r4 + cx24 * r2 * re2 + cx44 * re4
                     + cx06 * r6 + cx26 * r4 * re2 + cx46 * r2 * re4 + cx66 * re6;
      float fy = 1.0 + cy02 * r2 + cy22 * re2 + cy04 * r4 + cy24 * r2 * re2 + cy44 * re4
                     + cy06 * r6 + cy26 * r4 * re2 + cy46 * r2 * re4 + cy66 * re6;
      float xq = xr * fx * ana_pa * ana_sqx;
      float yq = yr * fy * ana_sqy;
      ux = cphi * xq - sphi * yq;
      uy = sphi * xq + cphi * yq;
    }

    float xu = center_x + ux * n;
    float yu = center_y + uy * n;
    dir = normalize(vector(xu / f_eff, yu / f_eff, 1.0));
  }

  if (!ok) {
    throughput = color(0.0);
    return;
  }

  /* Vignetting: polynomial in the distorted frame position + optional cos^4 falloff. */
  float rho2 = ((px - center_x) * (px - center_x) + (py - center_y) * (py - center_y)) / (half_diag * half_diag);
  float vig = max(1.0 + rho2 * (vig_k1 + rho2 * (vig_k2 + rho2 * vig_k3)), 0.0);
  if (cos4 > 0.0) {
    float cz = clamp(dir[2], 0.0, 1.0);
    vig *= 1.0 - cos4 + cos4 * cz * cz * cz * cz;
  }
  throughput = color(vig);

  /* Thin-lens depth of field; aperture radius f / (2N), focus plane at focus_dist. */
  if (dof_on) {
    point lens_p = point(0.0);
    getattribute("cam:aperture_position", lens_p);
    lens_p *= focal_length * 1e-3;
    point pfocus = (dir[2] > 1e-4) ? point(dir * (focus_dist / dir[2])) : point(dir * focus_dist);
    position = lens_p;
    direction = normalize(pfocus - lens_p);
  }
  else {
    direction = dir;
  }
"""


def _decl(p):
    if p.is_int:
        return f"    int {p.name} = {int(p.default)}"
    return f"    float {p.name} = {float(p.default)!r}"


def source():
    """Return the complete OSL source code."""
    params = ["    int model = 0", "    float focal_length = 50.0"]
    params += [_decl(p) for p in schema.ALL_PARAMS]
    params += [
        "    output point position = 0.0",
        "    output vector direction = 0.0",
        "    output color throughput = 1.0",
    ]
    head = "/* Lensder camera shader — generated by lensder/core/osl.py, do not edit. */\n"
    sig = f"shader {SHADER_NAME}(\n" + ",\n".join(params) + ")\n{"
    # The macro definition must precede the shader body.
    macro_end = _BODY.index("  vector sensor")
    return head + _BODY[:macro_end] + sig + "\n" + _BODY[macro_end:] + "}\n"


def source_hash():
    return hashlib.sha1(source().encode("utf-8")).hexdigest()[:12]


def shader_param_names():
    return ["model", "focal_length"] + [p.name for p in schema.ALL_PARAMS]
