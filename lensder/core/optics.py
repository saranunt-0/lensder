"""Camera-body / sensor geometry and depth-of-field helpers (no bpy)."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SensorFit:
    """How the Blender camera must be set up for a physical gate."""

    fit: str  # 'HORIZONTAL' or 'VERTICAL'
    sensor_width: float  # mm, desqueezed + overscan, value for Camera.sensor_width
    sensor_height: float  # mm, value for Camera.sensor_height
    frame_w: float  # mm, desqueezed frame actually recorded (no overscan)
    frame_h: float


def fit_gate(sensor_w, sensor_h, squeeze, render_aspect, overscan=1.0):
    """Fit the delivery frame inside the (desqueezed) sensor gate.

    The recorded frame is the largest rectangle of ``render_aspect``
    (width/height, pixel aspect included) inside the gate
    ``sensor_w * squeeze`` x ``sensor_h``. This is what a real camera does when
    you choose a recording format smaller than the full sensor.

    ``overscan`` (>= 1) widens the rendered field of view for post-process
    distortion. The recorded frame size is unchanged.
    """
    gate_w = sensor_w * squeeze
    gate_h = sensor_h
    if render_aspect >= gate_w / gate_h:
        frame_w, frame_h = gate_w, gate_w / render_aspect
        return SensorFit("HORIZONTAL", frame_w * overscan, gate_h * overscan, frame_w, frame_h)
    frame_w, frame_h = gate_h * render_aspect, gate_h
    return SensorFit("VERTICAL", gate_w * overscan, frame_h * overscan, frame_w, frame_h)


def fov_deg(size_mm, focal_mm):
    return math.degrees(2.0 * math.atan(size_mm * 0.5 / focal_mm))


def crop_factor(frame_w, frame_h):
    """Crop factor relative to the 36x24 full-frame diagonal."""
    return math.hypot(36.0, 24.0) / math.hypot(frame_w, frame_h)


def circle_of_confusion(frame_w, frame_h):
    """Conventional acceptable CoC: frame diagonal / 1500 (≈0.029 mm on FF)."""
    return math.hypot(frame_w, frame_h) / 1500.0


def depth_of_field(focal_mm, fstop, focus_m, coc_mm):
    """Return ``(near_m, far_m, hyperfocal_m)``; far may be ``math.inf``."""
    f = focal_mm
    hyper_mm = f * f / (fstop * coc_mm) + f
    s = focus_m * 1000.0
    near = s * (hyper_mm - f) / (hyper_mm + s - 2.0 * f)
    far = math.inf if s >= hyper_mm else s * (hyper_mm - f) / (hyper_mm - s)
    return near / 1000.0, far / 1000.0, hyper_mm / 1000.0
