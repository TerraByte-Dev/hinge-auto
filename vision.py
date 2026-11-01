"""Image-based detection for UI elements whose position varies per profile.

The compose box (Send Like button + comment input) anchors to whichever
element's heart was tapped, so its absolute y coordinate shifts based on
photo height and scroll state. Static COORDS in config don't survive
across profiles — we have to find these elements at tap-time.
"""

import io

import numpy as np
from PIL import Image
from scipy.ndimage import binary_opening, label, find_objects

import config


def _png_to_array(png: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(png)).convert("RGB"))


def find_send_like(png: bytes, explain: bool = False):
    """Locate the peach 'Send Like' button. Returns (x, y) center or None.

    The button is a wide peach pill (~595x109) sitting on the right side
    of the compose card. Some peach-toned prompt bubbles look similar in
    color and size, so we filter on:
      - width 500-700, height 80-130, area > 30000 (size of the pill)
      - x_center > 500 (Send Like is right-aligned; prompt pills are
        often left-aligned)
    When multiple candidates pass, pick the topmost — the compose card
    sits above any prompt elements visible below it.

    Newer Hinge builds (Galaxy S23) draw it as a flat (26,26,26) 'Send
    Priority Like' pill (~528x123) right of a lavender Rose pill. The
    keyboard toolbar is the same grey but spans the full screen width, so
    the width cap (< 700) drops it; the x_center > 500 rule keeps clicks
    off the Rose button.
    """
    arr = _png_to_array(png)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    peach = (
        (r > 220) & (g > 190) & (g < 235) & (b > 170) & (b < 220)
        & (r > g) & (g > b)
    )
    a = arr.astype(np.int16)
    black_pill = (np.abs(a - 26) <= 3).all(axis=-1)
    report: list[dict] | None = [] if explain else None
    candidates = (
        _pill_blobs(peach, (500, 700), (80, 130), "peach", 500, report)
        + _pill_blobs(black_pill, (400, 700), (90, 150), "black_pill", 500, report)
    )
    return _topmost(candidates, report)


def _topmost(candidates: list[tuple[int, int]], report: list[dict] | None):
    """Topmost (x, y) of `candidates`, or None; paired with `report` when
    explaining."""
    result = min(candidates, key=lambda c: (c[1], c[0])) if candidates else None
    return (result, report) if report is not None else result


def _blobs(mask: np.ndarray):
    """Yield (bbox, area) for each connected blob in `mask`."""
    labeled, _ = label(mask)
    for i, sl in enumerate(find_objects(labeled), 1):
        if sl is None:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        yield (x0, y0, x1, y1), int((labeled[sl] == i).sum())


def _note(report, mask_name, bbox, area, verdict) -> None:
    # Skip specks so incident reports stay readable.
    if report is not None and (bbox[2] - bbox[0] >= 30 or bbox[3] - bbox[1] >= 30):
        report.append({"mask": mask_name, "bbox": list(bbox), "area": area,
                       "verdict": verdict})


def _pill_blobs(
    mask: np.ndarray, width: tuple[int, int], height: tuple[int, int],
    mask_name: str, min_cx: int, report: list[dict] | None = None,
) -> list[tuple[int, int]]:
    """Centers (x, y) of blobs sized like a button pill (area > 30000),
    right of `min_cx`."""
    out = []
    for (x0, y0, x1, y1), area in _blobs(mask):
        w, h = x1 - x0, y1 - y0
        cx = (x0 + x1) // 2
        if not (width[0] < w < width[1] and height[0] < h < height[1]):
            verdict = "size"
        elif area <= 30000:
            verdict = "area"
        elif cx <= min_cx:
            verdict = f"x<={min_cx}"
        else:
            verdict = "accepted"
            out.append((cx, (y0 + y1) // 2))
        _note(report, mask_name, (x0, y0, x1, y1), area, verdict)
    return out


def find_first_heart(png: bytes, explain: bool = False):
    """Locate the heart icon on photo 1 (topmost heart in current view).

    Hinge's heart-on-photo widget is a white circle (~126x126, area ~10900)
    overlaid at the photo's bottom-right corner. Profile layouts vary —
    some have prompt headers above photo 1 ('Me in the wild'), others
    don't — so photo 1's heart y position drifts profile-to-profile.

    Strategy: find white near-circular blobs of the right size, return
    the topmost one (lowest y) since that's photo 1 when scrolled to top.

    Filters tuned to reject clothing false-positives (white dresses, shirts
    can blob-match by raw size). Real Hinge hearts are always:
      - right-aligned (x_center > 800) — the icon sits at the photo's
        bottom-right corner
      - near-circular (|w-h| < 15) — clothing blobs are elongated
      - area >= 8500 — clothing fragments tend to be smaller circular
        regions; the heart's white circle is ~10800-11000 px

    Newer Hinge builds (seen on a Galaxy S23, 1080x2340) draw the heart as
    a solid (26,26,26) circle ~144px across with a white glyph. Dark photo
    pixels touch it, so that mask matches the exact flat grey rather than
    "dark", and a 13x13 opening cuts the thin runs of that same grey in
    dark clothing that would otherwise still join the circle's blob.
    """
    arr = _png_to_array(png)
    white = (arr[..., 0] > 235) & (arr[..., 1] > 235) & (arr[..., 2] > 235)
    a = arr.astype(np.int16)
    dark_grey = (
        (np.abs(a[..., 0] - 26) <= 3)
        & (np.abs(a[..., 1] - 26) <= 3)
        & (np.abs(a[..., 2] - 26) <= 3)
    )
    dark_grey = binary_opening(dark_grey, structure=np.ones((13, 13)))
    report: list[dict] | None = [] if explain else None
    hearts = (
        _round_blobs(white, (100, 140), (8500, 12000), "white", report)
        + _round_blobs(dark_grey, (125, 165), (9000, 17000), "dark_grey", report)
    )
    return _topmost(hearts, report)


def _round_blobs(
    mask: np.ndarray, size: tuple[int, int], area: tuple[int, int],
    mask_name: str, report: list[dict] | None = None, min_cx: int = 800,
) -> list[tuple[int, int]]:
    """Centers (x, y) of near-square blobs with side in `size`, pixel count
    in `area`, right of `min_cx` (hearts sit at the photo's right edge)."""
    out = []
    for (x0, y0, x1, y1), n in _blobs(mask):
        w, h = x1 - x0, y1 - y0
        cx = (x0 + x1) // 2
        if not (size[0] < h < size[1] and size[0] < w < size[1]):
            verdict = "size"
        elif abs(w - h) >= 15:
            verdict = "aspect"
        elif n < area[0] or n > area[1]:
            verdict = "area"
        elif cx <= min_cx:
            verdict = f"x<={min_cx}"
        else:
            verdict = "accepted"
            out.append((cx, (y0 + y1) // 2))
        _note(report, mask_name, (x0, y0, x1, y1), n, verdict)
    return out


def comment_field_text_pixels(png: bytes, send_like_xy: tuple[int, int]) -> int:
    """Count dark (text) pixels in the comment input area above Send Like.

    Empty field shows only faint grey placeholder ('Add a comment') — very
    few dark pixels. A filled field has many dark pixels from typed text.
    Used to verify that `adb shell input text` actually landed before
    Send Like fires (events can drop under host CPU contention).
    """
    arr = _png_to_array(png)
    _, sy = send_like_xy
    top, bottom = config.COMPOSE["text_window"]
    y0 = max(0, sy - top)
    y1 = max(0, sy - bottom)
    # Full card width: typed text starts at the box's left edge, which
    # can sit far left of Send Like's center.
    region = arr[y0:y1, 60:arr.shape[1] - 60]
    if region.size == 0:
        return 0
    # Near-black only: typed text's last line is (26,26,26), while the empty
    # box's "Add a comment" placeholder and the S23's faded scrolled-up line
    # are grey (88-133) and must not count as text.
    dark = (region.max(axis=-1) < 60).sum()
    return int(dark)


def find_comment_input(send_like_xy: tuple[int, int]) -> tuple[int, int]:
    """Comment input sits at a fixed offset above the Send Like button.

    Offset is per device (config.COMPOSE['comment_dy']: 171 on the
    emulator, 200 on the S23); x ~540 (centered in the compose card).
    Stable because the card's internal layout is fixed; only the whole
    card shifts.
    """
    _, send_y = send_like_xy
    return (540, send_y - config.COMPOSE["comment_dy"])
