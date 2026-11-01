"""Drive Hinge's in-app filter UI via ADB.

Currently supports the Age filter only. Coords live in `filter_coords.json`
(per-device, gitignored). Generate by running `python calibrate_filters.py`
or by hand-editing if you already know the layout.

The Age sheet's two slider thumbs share a slope (px-per-year) but have
different x-anchors — each thumb's center is offset by the thumb radius
relative to its logical position on the track. Calibration records each
thumb's anchor separately.

`set_age_range(age_min, age_max)`:
  1. Tap the Age chip on Discover -> bottom sheet animates up.
  2. Detect current thumb x-positions by thickness-thresholded pixel scan.
  3. Compute target x for each thumb via linear map from calibration.
  4. Swipe each thumb to its target. Drag-order avoids thumb collision.
  5. Tap Apply -> sheet dismisses, filter takes effect.

Hinge+/X may be required for custom age ranges on some accounts. If the
slider snaps back after Apply, the loop runner will surface this on the
next profile via the age-gate skip path.
"""

import io
import time
from pathlib import Path

import numpy as np
from PIL import Image

import adb
import config


_COORDS_FILE = config.BASE_DIR / "filter_coords.json"

# Label that must be on screen after this flow's first navigation tap
# (ui.require_label). Read from the phone with ui.ui_texts(); None =
# not captured yet, check skipped.
AGE_SHEET_LABEL: str | None = "Age"


def load_coords() -> dict:
    return config.load_device_json(_COORDS_FILE, "python calibrate_filters.py")


def find_age_thumbs(
    png: bytes,
    slider_y: int,
    half_h: int = 35,
    min_thickness: int = 25,
    thresh: int = 60,
) -> list[int]:
    """Return x-centers of slider thumbs on the Age sheet, ordered left->right.

    Strategy: in a horizontal strip centered on slider_y, count dark pixels per
    column. Track is thin (~10px); thumbs are ~80px circles. A column with
    >= min_thickness dark pixels is "in a thumb". Runs of such columns wider
    than 30px are thumbs; their midpoints are the centers.
    """
    im = np.array(Image.open(io.BytesIO(png)).convert("L"))
    strip = im[slider_y - half_h : slider_y + half_h]
    thickness = (strip < thresh).sum(axis=0)
    is_thumb = thickness >= min_thickness
    runs: list[int] = []
    in_run = False
    start = 0
    for x, t in enumerate(is_thumb):
        if t and not in_run:
            in_run = True
            start = x
        elif not t and in_run:
            in_run = False
            if x - start > 30:
                runs.append((start + x - 1) // 2)
    if in_run:
        runs.append((start + len(is_thumb) - 1) // 2)
    return runs


def _age_to_x(age: int, thumb_cal: dict, min_age: int, max_age: int) -> int:
    age = max(min_age, min(max_age, age))
    return round(
        thumb_cal["x_anchor"]
        + (age - thumb_cal["age_anchor"]) * thumb_cal["slope_px_per_year"]
    )


def _drag(from_x: int, to_x: int, slider_y: int) -> None:
    if abs(from_x - to_x) < 3:
        return
    adb.swipe(from_x, slider_y, to_x, slider_y, 400)
    time.sleep(0.6)


def set_age_range(age_min: int, age_max: int) -> None:
    """Drive Hinge's Age filter to (age_min, age_max). Hinge must be on the
    Discover tab when called. Raises if calibration is missing or the sheet
    doesn't open as expected.
    """
    if age_min > age_max:
        raise ValueError(f"age_min ({age_min}) > age_max ({age_max})")
    coords = load_coords()
    slider_y = coords["slider_y"]
    min_age = coords.get("min_age", 18)
    max_age = coords.get("max_age", 85)

    target_left = _age_to_x(age_min, coords["left_thumb"], min_age, max_age)
    target_right = _age_to_x(age_max, coords["right_thumb"], min_age, max_age)

    # 1. Open Age sheet
    adb.tap(*coords["age_chip"], target="age_chip")
    time.sleep(1.0)
    import ui
    ui.require_label(AGE_SHEET_LABEL, step="filters: Age sheet")
    time.sleep(1.2)

    # 2. Detect current thumb positions
    png = adb.screenshot()
    thumbs = find_age_thumbs(png, slider_y)
    if len(thumbs) != 2:
        raise RuntimeError(
            f"Expected 2 slider thumbs at y={slider_y}, found {len(thumbs)}: "
            f"{thumbs}. Age sheet may not have opened; re-check the age_chip "
            f"coord in filter_coords.json."
        )
    current_left, current_right = thumbs

    # 3. Drag, ordering by collision-risk. Hinge prevents the two thumbs from
    # crossing, but if we aim a swipe end too close to the other thumb the
    # gesture can land on the wrong one. Drag the safer one first.
    if target_left > current_right - 80:
        _drag(current_right, target_right, slider_y)
        _drag(current_left, target_left, slider_y)
    else:
        _drag(current_left, target_left, slider_y)
        _drag(current_right, target_right, slider_y)

    # 4. Apply
    time.sleep(0.4)
    adb.tap(*coords["apply_button"])
    time.sleep(1.5)


def dismiss_age_sheet() -> None:
    """Close the Age bottom sheet without applying (tap the dimmed backdrop)."""
    coords = load_coords()
    adb.tap(*coords["dismiss_tap"])
    time.sleep(0.8)
