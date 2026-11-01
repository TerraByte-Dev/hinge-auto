"""Interactive calibration for the in-app Age filter.

Walks through 3 captures:
  1. Discover -> tap Age chip -> note thumb positions + displayed ages (start)
  2. Drag left thumb to far left -> note new positions + displayed ages
  3. Drag right thumb to far right -> note new positions + displayed ages

Then computes:
  - left_thumb.x_anchor / slope_px_per_year   (from points 1 + 2 of LEFT thumb)
  - right_thumb.x_anchor / slope_px_per_year  (from points 1 + 3 of RIGHT thumb)
  - min_age / max_age (the actual snap-extreme ages displayed)

Writes filter_coords.json. Dismisses the sheet without applying so your real
filter is unchanged.

Prerequisite: Hinge open on Discover tab, emulator connected via ADB.

You will be asked to type the AGE NUMBER displayed above each thumb at each
step (the script can't reliably OCR Hinge's font). Thumb pixel positions are
auto-detected.
"""

import json
import sys
import time
from pathlib import Path

import adb
import config
import filters


_DEFAULT_AGE_CHIP = tuple(config.COORDS["age_chip"])
_DEFAULT_SLIDER_Y = config.FILTER_CAL["slider_y"]
_DEFAULT_APPLY_BUTTON = tuple(config.FILTER_CAL["apply_button"])
_DEFAULT_DISMISS_TAP = tuple(config.FILTER_CAL["dismiss_tap"])
_OUTPUT_FILE = config.BASE_DIR / "filter_coords.json"
_CAL_DIR = config.BASE_DIR  # save screenshots here for inspection


def _prompt_int(label: str, default: int | None = None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        s = input(f"{label}{suffix}: ").strip()
        if not s and default is not None:
            return default
        try:
            return int(s)
        except ValueError:
            print("  not an integer, try again")


def _capture(name: str) -> bytes:
    png = adb.screenshot()
    path = _CAL_DIR / f"filter_cal_{name}.png"
    path.write_bytes(png)
    print(f"  saved {path.name}")
    return png


def compute_slopes(
    start_left_x: int, start_left_age: int, min_left_x: int, min_age: int,
    start_right_x: int, start_right_age: int, max_right_x: int, max_age: int,
) -> tuple[float, float]:
    """px-per-year for the left and right thumbs.

    A thumb that already sits at its end of the track (e.g. min age 18)
    can't move, so its slope can't be measured; the thumbs share a slope,
    so it borrows the other thumb's. Raises ValueError if neither moved.
    """
    left = right = None
    if start_left_age != min_age:
        left = (start_left_x - min_left_x) / (start_left_age - min_age)
    if max_age != start_right_age:
        right = (max_right_x - start_right_x) / (max_age - start_right_age)
    if left is None and right is None:
        raise ValueError("neither thumb moved (range already spans min..max)")
    return (left if left is not None else right,
            right if right is not None else left)


def main() -> int:
    adb.check_device()
    print("Hinge filter Age slider — calibration.")
    print("Make sure Hinge is open on the Discover tab. Press ENTER to continue.")
    input()

    # --- Step 0: Discover state, age chip position ---
    print("\n[Step 1/3] Capturing Discover state.")
    _capture("00_discover")
    print(f"  Default Age-chip coord is {_DEFAULT_AGE_CHIP}. Press ENTER to use it,")
    print(f"  or type 'x,y' to override (read off your screen with Paint or similar).")
    raw = input("  age_chip: ").strip()
    if raw:
        cx, cy = (int(s) for s in raw.split(","))
        age_chip = (cx, cy)
    else:
        age_chip = _DEFAULT_AGE_CHIP

    # --- Step 1: tap chip, read current state ---
    print("\n[Step 2/3] Tapping Age chip and reading current thumb positions.")
    adb.tap(*age_chip)
    time.sleep(1.2)
    png = _capture("01_age_tapped")
    thumbs = filters.find_age_thumbs(png, _DEFAULT_SLIDER_Y)
    if len(thumbs) != 2:
        print(f"  ERROR: expected 2 thumbs, found {len(thumbs)}: {thumbs}")
        print("  Either age_chip didn't open the sheet, or slider_y is wrong.")
        return 1
    start_left_x, start_right_x = thumbs
    print(f"  Detected thumbs at x={start_left_x}, x={start_right_x}.")
    start_left_age = _prompt_int("  Age above LEFT thumb")
    start_right_age = _prompt_int("  Age above RIGHT thumb")

    # --- Step 2: drag left thumb all the way left ---
    print("\n[Step 3/3a] Dragging LEFT thumb to far left.")
    adb.swipe(start_left_x, _DEFAULT_SLIDER_Y, 50, _DEFAULT_SLIDER_Y, 600)
    time.sleep(1.0)
    png = _capture("02_left_min")
    thumbs = filters.find_age_thumbs(png, _DEFAULT_SLIDER_Y)
    if len(thumbs) != 2:
        print(f"  ERROR: lost a thumb: {thumbs}")
        return 1
    min_left_x = thumbs[0]
    print(f"  Left thumb now at x={min_left_x}.")
    min_age = _prompt_int("  Age above LEFT thumb (probably 18)", 18)

    # --- Step 3: drag right thumb all the way right ---
    print("\n[Step 3/3b] Dragging RIGHT thumb to far right.")
    adb.swipe(start_right_x, _DEFAULT_SLIDER_Y, 1030, _DEFAULT_SLIDER_Y, 800)
    time.sleep(1.0)
    png = _capture("03_right_max")
    thumbs = filters.find_age_thumbs(png, _DEFAULT_SLIDER_Y)
    if len(thumbs) != 2:
        print(f"  ERROR: lost a thumb: {thumbs}")
        return 1
    max_right_x = thumbs[1]
    print(f"  Right thumb now at x={max_right_x}.")
    print("  Hinge shows the max as 'NN+' (e.g. '85+'). Type just the number.")
    max_age = _prompt_int("  Age above RIGHT thumb", 85)

    # --- Compute calibration ---
    try:
        left_slope, right_slope = compute_slopes(
            start_left_x, start_left_age, min_left_x, min_age,
            start_right_x, start_right_age, max_right_x, max_age)
    except ValueError as e:
        print(f"  ERROR: {e}. Widen your age range in Hinge first, then re-run.")
        adb.tap(*_DEFAULT_DISMISS_TAP)
        return 1
    coords = {
        "_doc": (
            "Generated by calibrate_filters.py. Each thumb has its own "
            "x_anchor / slope because thumb-center x is offset by the "
            "thumb-radius from the logical position on the track."
        ),
        "screen_size": [config.SCREEN_WIDTH, config.SCREEN_HEIGHT],
        "age_chip": list(age_chip),
        "slider_y": _DEFAULT_SLIDER_Y,
        "left_thumb": {
            "age_anchor": start_left_age,
            "x_anchor": start_left_x,
            "slope_px_per_year": round(left_slope, 3),
        },
        "right_thumb": {
            "age_anchor": start_right_age,
            "x_anchor": start_right_x,
            "slope_px_per_year": round(right_slope, 3),
        },
        "min_age": min_age,
        "max_age": max_age,
        "apply_button": list(_DEFAULT_APPLY_BUTTON),
        "dismiss_tap": list(_DEFAULT_DISMISS_TAP),
    }

    # --- Dismiss without applying ---
    print("\nDismissing Age sheet without applying (your real filter is unchanged).")
    adb.tap(*_DEFAULT_DISMISS_TAP)
    time.sleep(0.8)

    _OUTPUT_FILE.write_text(json.dumps(coords, indent=2))
    print(f"\nWrote {_OUTPUT_FILE.name}")
    print(f"  left_thumb:  age {start_left_age} -> x {start_left_x}, slope {left_slope:.2f} px/yr")
    print(f"  right_thumb: age {start_right_age} -> x {start_right_x}, slope {right_slope:.2f} px/yr")
    print(f"  range: {min_age} - {max_age}")
    print("\nTest with:  python -c \"import filters; filters.set_age_range(33, 44)\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
