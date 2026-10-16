"""Exposure triangle, shutter and white-balance conversions (no bpy).

Absolute photometric calibration is intentionally not attempted: Blender's
light units make "correct" absolute exposure scene-dependent. Instead, manual
exposure is measured in stops relative to a reference ("metered") setting,
which is how photographers think about it anyway.
"""

import math

# Neutral tint in Blender's white balance (verified on 5.2: 6500 K + tint 10 = D65).
BLENDER_NEUTRAL_TINT = 10.0

STANDARD_FSTOPS = (1.0, 1.2, 1.4, 1.8, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0)
STANDARD_ISO = (50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600)
STANDARD_SHUTTER = (8000, 4000, 2000, 1000, 500, 250, 125, 60, 50, 48, 30, 25, 24, 15, 8, 4, 2, 1)


def ev100(fstop, shutter_s, iso):
    """Exposure value normalised to ISO 100 (higher = darker image)."""
    return math.log2(fstop * fstop / shutter_s) - math.log2(iso / 100.0)


def relative_stops(fstop, shutter_s, iso, ref_fstop, ref_shutter_s, ref_iso):
    """Stops brighter (+) or darker (-) than the reference setting."""
    return ev100(ref_fstop, ref_shutter_s, ref_iso) - ev100(fstop, shutter_s, iso)


def shutter_seconds_from_angle(angle_deg, fps):
    return (angle_deg / 360.0) / fps


def shutter_angle_from_seconds(seconds, fps):
    return seconds * fps * 360.0


def motion_blur_fraction(seconds, fps):
    """Blender's ``render.motion_blur_shutter`` (fraction of one frame)."""
    return seconds * fps


def blender_tint(camera_tint):
    """Camera-style tint (0 = neutral, + = magenta) -> Blender tint."""
    return camera_tint + BLENDER_NEUTRAL_TINT


def format_shutter(seconds):
    if seconds >= 0.5:
        return f'{seconds:.1f}"'
    return f"1/{round(1.0 / seconds)}"
