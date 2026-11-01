"""Drive Hinge's in-app Location (MyMove / "My neighborhood") via ADB.

Two JSON files:
  - `location_coords.json` — UI element coords (per-device, gitignored)
  - `locations.json`       — short-name -> Hinge-friendly search string map
    (committed; add cities as needed)

`set_location(name)`:
  1. Resolve `name` via locations.json (or use raw string if not mapped).
  2. From Discover: tap sliders icon -> Dating Preferences screen.
  3. Tap "My neighborhood" row -> Location picker.
  4. Tap text input -> tap X to clear any prior value -> type the city.
  5. Wait for autocomplete -> tap first suggestion.
  6. Tap back arrow -> commit and return to Preferences.
  7. Close Preferences (X top-left) -> back to Discover.

Verification is light — we trust the autocomplete's top suggestion to match
the typed city. If the first suggestion is wrong (e.g. an airport beats the
city in autocomplete for an obscure name), add a curated entry in
locations.json with a more specific search string (e.g. "Portland, OR").

Hinge+/X (MyMove) is required to change to a city outside your area.
Without it, the change may snap back. No verification of that here for v1.
"""

import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

import adb
import config


_COORDS_FILE = config.BASE_DIR / "location_coords.json"

# Label that must be on screen after this flow's first navigation tap
# (ui.require_label). Read from the phone with ui.ui_texts(); None =
# not captured yet, check skipped.
NEIGHBORHOOD_LABEL: str | None = "My neighbourhood"
_NAMES_FILE = config.BASE_DIR / "locations.json"


def load_coords() -> dict:
    return config.load_device_json(
        _COORDS_FILE, "fill location_coords.json from phone screenshots")


def load_names() -> dict:
    if not _NAMES_FILE.exists():
        return {}
    data = json.loads(_NAMES_FILE.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def resolve(name: str) -> str:
    """Map a short name to a Hinge search string. Unmapped names pass through."""
    names = load_names()
    return names.get(name.lower(), name)


def get_rotation(name: str) -> list[str]:
    """Return the ordered rotation list named `name` from locations.json."""
    if not _NAMES_FILE.exists():
        raise RuntimeError(f"{_NAMES_FILE.name} missing")
    data = json.loads(_NAMES_FILE.read_text())
    rotations = data.get("_rotations", {})
    rotations = {k: v for k, v in rotations.items() if not k.startswith("_")}
    if name not in rotations:
        raise KeyError(
            f"Unknown rotation {name!r}. Available: {sorted(rotations)}"
        )
    return rotations[name]


# Exact on-screen label of Hinge's "seen everyone" empty state on the
# phone, read with ui.ui_texts() the first time it's seen. While None,
# --rotate refuses to start on a non-emulator device.
EMPTY_STATE_LABEL: str | None = None


def is_out_of_candidates(png: bytes, nodes: list | None = None) -> bool:
    """True on Hinge's 'You've seen everyone for now' empty-state screen.

    Emulator: the pixel check below alone. Phone: the pixel check is only a
    cheap pre-filter — the S23's (26,26,26) photo heart can sit in the same
    patch — so it must also find EMPTY_STATE_LABEL among the on-screen
    labels (`nodes`, or a fresh ui.ui_texts() dump).
    """
    if not _empty_state_pixels(png):
        return False
    if config.DEVICE == "emulator":
        return True
    if EMPTY_STATE_LABEL is None:
        return False
    import ui
    if nodes is None:
        try:
            nodes = ui.ui_texts()
        except ui.UiDumpFailed:
            return False
    return ui.find_label(nodes, EMPTY_STATE_LABEL) is not None


def _empty_state_pixels(png: bytes) -> bool:
    """Pixel check for the 'You've seen everyone for now' empty-state screen.

    Identified by the solid black 'Change filters' pill button at the
    center of the screen. The button spans roughly x=120-960, y=1400-1500
    with a flat dark fill — distinct from profile photos (variation) or
    chip-row UI (light gray).

    Conservative: requires both a very low mean luminance (uniform dark)
    AND low std (no photographic texture) inside the expected button rect.
    """
    im = np.array(Image.open(io.BytesIO(png)).convert("L"))
    h, w = im.shape
    if h < 1550 or w < 1000:
        return False
    # "Change filters" button spans y=1420-1530, x=100-980. The right
    # side of the pill (x=800-900) is solid black with no text or edges
    # — clean sample region (observed mean=26, std=0 on empty-state).
    # On any other screen this region is light (white bg / chip row /
    # profile photos), so the dark+uniform check is reliable.
    inner = im[1450:1500, 800:900]
    if inner.size == 0:
        return False
    return inner.mean() < 50 and inner.std() < 15


def set_location(name: str) -> None:
    """Change Hinge's "My neighborhood" to the city resolved from `name`.

    Hinge must be on the Discover tab when called. On success, returns to
    Discover with the new location committed.
    """
    search_string = resolve(name)
    coords = load_coords()
    d = coords["delays"]

    # 0. Canonicalize to Discover tab. Tapping the H logo in the bottom nav
    # is a no-op if we're already on Discover, but brings us back if we
    # were on Preferences / a profile / another tab. Without this, the
    # sliders_icon tap can fall on the wrong UI element if the screen
    # state isn't fresh Discover (observed: starting on Preferences makes
    # the (95,220) tap hit the close X instead of the sliders icon).
    adb.tap(*coords["discover_tab"])
    time.sleep(1.0)

    # 1. Discover -> sliders icon -> Dating Preferences
    adb.tap(*coords["sliders_icon"], target="sliders_icon")
    time.sleep(d["after_sliders_tap"])
    import ui
    nodes = ui.require_label(NEIGHBORHOOD_LABEL, step="location: Dating Preferences")

    # 2. Tap "My neighborhood" row -> Location picker. Prefer the label's
    # live position: a dismissible banner above the rows shifts them.
    row = ui.find_label(nodes, NEIGHBORHOOD_LABEL) if nodes else None
    adb.tap(*(row.center if row else coords["neighborhood_row"]),
            target="neighborhood_row")
    time.sleep(d["after_neighborhood_tap"])

    # 3. Tap text input
    adb.tap(*coords["text_input"])
    time.sleep(d["after_input_tap"])

    # 4. Tap X to clear any existing value already in the field.
    # Idempotent — no-op if field is empty.
    adb.tap(*coords["input_clear_x"])
    time.sleep(d["after_clear_tap"])

    # 5. Type the search string
    adb.input_text(search_string)
    time.sleep(d["after_typing"])

    # 6. Tap first suggestion. Wait for map + state to settle before
    # tapping back, otherwise the commit doesn't take (observed flake at
    # 2.5s, reliable at 3.5s on this emulator).
    adb.tap(*coords["first_suggestion"])
    time.sleep(d["after_suggestion_tap"])

    # 7. Back arrow commits and returns to Preferences
    adb.tap(*coords["back_arrow"])
    time.sleep(d["after_back"])

    # 8. Return to Discover. Two taps for robustness: first the close-X
    # on Preferences, then the Discover tab in the bottom nav. The X
    # alone sometimes mis-lands (observed: subsequent force-skip taps
    # then drilled into sub-screens like "Education level" instead of
    # skipping profiles); the Discover-tab tap is a universal recovery
    # from any state.
    adb.tap(*coords["preferences_close"])
    time.sleep(d["after_close"])
    adb.tap(*coords["discover_tab"])
    time.sleep(1.0)


def current_neighborhood() -> str | None:
    """Read the current 'My neighborhood' value from the Preferences screen.

    Opens Preferences, screenshots, OCR-extracts the value, closes Preferences.
    Returns None if the field isn't found (e.g. UI changed).

    Not implemented in v1 — would need OCR or a fixed-coord text region read.
    """
    raise NotImplementedError(
        "current_neighborhood() not yet implemented. Inspect manually for v1."
    )
