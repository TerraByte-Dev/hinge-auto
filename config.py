"""Configuration for HingeAuto.

PREFERENCES, AGE_MIN/MAX, and MESSAGE_VOICE come from the active mode (see
`modes/`). Set ACTIVE_MODE here for the persistent default; override per-run
via `python main.py --mode <name>`.

The COORDS defaults below are calibrated for a Pixel 10 emulator
(1080x2424). If that's what you're running and Hinge hasn't shifted
its layout, they should work as-is. Otherwise run `python calibrate.py`
and update the values that don't match your device.
"""

from pathlib import Path

# ---------- Mode selection ----------
# Which `modes/<name>.py` to load. Overridden per-run by `python main.py --mode X`.
ACTIVE_MODE = "example_lenient"

# These get filled in by _apply_mode() at the bottom of this file. Declared
# here so static analyzers / IDEs see them. Do not edit by hand — edit the
# mode module instead.
PREFERENCES: str = ""
AGE_MIN: int | None = None
AGE_MAX: int | None = None
MESSAGE_VOICE: str | None = None
MODE_NAME: str = ""
PREMADES: list[dict] = []

# ---------- Run mode ----------
# DRY_RUN = False -> actually like / send messages (default)
# DRY_RUN = True  -> decide and log, but force-skip every profile
#                    instead of liking. Every "would-like" profile gets
#                    skipped (gone from your queue) but no likes are
#                    spent.
#
# When to flip this to True:
#   - Free Hinge (8 likes/day): YES, for your first run or two. Lets
#     you watch decisions without spending your daily cap on a rubric
#     you haven't tuned. Once decisions look right, flip back to False.
#   - Hinge+ (unlimited likes): NO. Just run small live batches
#     (MAX_LIKES_PER_SESSION = 5) and Ctrl-C if something looks off.
DRY_RUN = True

# Default = 8, which matches free-tier Hinge's daily like cap (resets at
# 4am local). One session per day exhausts the free allotment cleanly.
#
# If you have Hinge+ (no daily cap), bump this to ~25-50 per session and
# run multiple sessions throughout the day. Going much higher per session
# tends to trigger Hinge's soft-throttle (empty Discover after a burst);
# spacing batches across the day works better than one giant batch.
MAX_LIKES_PER_SESSION = 8
MAX_PROFILES_PER_SESSION = 100

# ---------- Emulator settings ----------
# Pixel 10 (and recent Pixel models) are 1080x2424. Change if you're using
# a different emulator profile — and re-run calibrate.py after any change.
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 2424

# Number of scroll-and-screenshot passes per profile.
# Longer profiles (6 photos + 3 prompts) need ~7 frames at the scroll step
# below. Duplicate end frames on shorter profiles are harmless.
FRAMES_PER_PROFILE = 7

# ---------- Coordinates ----------
# Pixel 10 emulator defaults (1080x2424). These values are
# accurate for the standard Pixel 10 AVD; if your screen is the same
# resolution and Hinge's layout hasn't shifted, they'll work as-is. If
# anything is off, run `python calibrate.py` to capture a screenshot and
# update the values that don't match.
COORDS = {
    # Skip / like action targets (Discover screen, photo 1 at top)
    "skip_button":       (134, 2068),   # X icon on prompt/photo card
    "heart_photo_1":     (938, 1421),   # Heart icon on photo 1

    # Compose box (anchors to the element whose heart was tapped; these
    # values are mostly fallbacks — vision.py re-finds them at tap-time
    # because the box shifts per profile).
    "send_like_button":  (687, 1488),
    "comment_input":     (540, 1317),
    "compose_close":     (960, 200),

    # Scroll gesture (swipe up = scroll down through profile).
    "scroll_from":       (540, 1700),
    "scroll_to":         (540, 700),
    "scroll_duration_ms": 350,

    # Bottom nav (5 evenly-spaced icons across the bottom strip).
    "nav_discover":      (108, 2270),
    "nav_standouts":     (324, 2270),
    "nav_likes_you":     (540, 2270),
    "nav_matches":       (756, 2270),
    "nav_self_pfp":      (972, 2270),

    # Self-profile flow (used by scan_self.py — "what does my profile
    # look like to others"). From Discover: nav_self_pfp → self_avatar →
    # view_tab gets you to a scrollable view of your own profile. Then
    # back_arrow → back_arrow → nav_discover to return.
    "self_avatar":       (540, 497),    # circle avatar on self tab
    "view_tab":          (810, 310),    # 'View' tab on profile editor
    "back_arrow":        (65, 200),     # top-left back arrow

    # Discover filter row (tap the chip to open its bottom sheet).
    "sliders_icon":      (95, 225),     # opens Dating Preferences
    "age_chip":          (460, 225),    # opens Age filter sheet

    # Matches tab (matches_scan.py): scroll gesture through the list and
    # the 'Their turn' section header that expands it.
    "matches_scroll_from":       (540, 1900),
    "matches_scroll_to":         (540, 1500),
    "matches_expand_their_turn": (540, 1264),
}

# Compose card geometry, relative to the Send Like button's center:
#   comment_dy  — comment input center sits this many px above Send Like
#   text_window — (top, bottom) px above Send Like bounding the rows where
#                 typed comment text appears (used to verify typing landed)
#   text_min_pixels — near-black pixels in that window that mean "text is
#                 there" (above the blinking cursor's ~150-300). Not
#                 length-scaled: the S23 box scrolls and shows ~2 lines
#                 however long the opener is (real typed boxes: 1614, 2424).
COMPOSE = {
    "comment_dy": 171,
    "text_window": (230, 60),
    "text_min_pixels": 600,
}

# Age-filter sheet geometry used by calibrate_filters.py (emulator values;
# devices override via their profile's "filter_cal").
FILTER_CAL = {
    "slider_y": 1720,
    "apply_button": (540, 2240),
    "dismiss_tap": (540, 400),
}

# Back taps from the self-profile View tab to a screen with the bottom nav
# (scan_self.return_to_discover).
SELF_VIEW_BACK_TAPS = 2

# ---------- Device selection ----------
# Which device to drive. "emulator" uses the defaults above. Any other
# profile pins adb to its serial (so a running emulator can't steal the
# taps) and overrides the screen size + whichever COORDS differ.
DEVICE = "galaxy_s23"

# adb serial to target; None = adb's default (first/only device).
ADB_SERIAL: str | None = None

# Dark/white pixel counts in the 120x120 patch at COORDS["skip_button"]
# (see main.skip_patch_counts). main.preflight compares the live screen to
# this (+-10%) to catch a moved layout before the loop taps anything.
# None = skip that check.
SKIP_FINGERPRINT: dict | None = None

DEVICES = {
    "emulator": {},
    # Samsung Galaxy S23 (SM-S911B), Android 15 / One UI 7, 1080x2340.
    # Hinge doesn't set FLAG_SECURE here, so plain `adb screencap` works.
    "galaxy_s23": {
        "serial": "RZCX920XP8L",
        "screen": (1080, 2340),
        "skip_fingerprint": {"dark": 1657, "white": 12500},
        # Measured from screencap on 2026-11-01. Heart / Send Like are
        # found by vision.py at tap-time; these are the static targets.
        "coords": {
            "skip_button":   (152, 1865),   # floating X, bottom-left
            "heart_photo_1": (917, 1388),
            "nav_discover":  (110, 2106),
            "nav_standouts": (323, 2106),
            "nav_likes_you": (536, 2106),
            "nav_matches":   (749, 2106),
            "nav_self_pfp":  (966, 2106),
            "sliders_icon":  (101, 180),
            "age_chip":      (529, 180),
            # Profile Hub avatar -> profile editor 'View' tab; one Back from
            # View returns to Profile Hub (labels "Profile", "View", "Back").
            "self_avatar":   (540, 484),
            "view_tab":      (810, 287),
            "back_arrow":    (78, 142),
        },
        "self_view_back_taps": 1,
        # Age sheet (labels "Minimum age"/"Maximum age" at y 1336,
        # "Apply filter", scrim "Close sheet" above y 563).
        "filter_cal": {
            "slider_y": 1336,
            "apply_button": (540, 2052),
            "dismiss_tap": (540, 281),
        },
        # Comment box spans y 868-1068 with Send Like centered at 1168.
        "compose": {
            "comment_dy": 200,
            "text_window": (290, 110),
        },
    },
}

# ---------- Timing ----------
# Random delay between actions (seconds, min/max for jitter).
DELAYS = {
    "after_scroll":     (0.6, 1.0),
    "after_screenshot": (0.2, 0.4),
    "after_tap":        (0.8, 1.4),
    "after_like_sent":  (2.5, 4.0),
    "after_skip":       (1.5, 2.5),
}

# ---------- Judge backend ----------
# "anthropic" -> uses your ANTHROPIC_API_KEY; best quality, ~$0.02-0.05/profile.
# "ollama"    -> uses Ollama Cloud (free tier) or local Ollama; lower quality
#                but no per-token cost.
JUDGE_BACKEND = "anthropic"

# ---------- Anthropic settings (when JUDGE_BACKEND == "anthropic") ----------
# Sonnet is the default — cheaper than Opus and plenty capable for this task.
# Switch to "claude-opus-4-7" if you want top-quality judgment, or
# "claude-haiku-4-5" for cheapest (may miss subtle cues).
MODEL = "claude-sonnet-4-6"
EFFORT = "medium"  # low | medium | high

# ---------- Opener generation (both backends) ----------
# Stage 1 (the vision call) drafts up to OPENER_CANDIDATES openers (max 4);
# a free deterministic filter then drops bad ones, and stage 2 (one or two
# text-only calls per like) picks one or asks for a rewrite. Rewrite cap: at
# most one rewrite; OPENER_RETRY above 1 has no effect. Any failure means a
# like with no comment, never a skip.
OPENER_CANDIDATES = 3
OPENER_RETRY = 1
# OPENER_SELECT = False skips stage 2 and sends the stage-1 pick if it passes
# the deterministic filter. Weaker: it LOSES the semantic checks (invented
# shared experience / sender biography, reads like a line, makes sense on
# photo 1) that only stage 2 performs.
OPENER_SELECT = True

# Profile identity (spec §6): the judged profile's top-of-profile thumbnail
# (grayscale, region crop of frames[0], resized to thumb_size = (w, h)) must
# near-match the live top view (mean abs diff <= thumb_max_diff, 0-255)
# before any heart tap; after Send, a near-match means "not advanced".
IDENTITY = {"thumb_size": (64, 128), "thumb_max_diff": 6.0}

# Prompt targeting (spec §6, phone only): accept the best "Prompt:" node
# scoring >= prompt_min_score against the opener's anchor whose normalised
# question alone has difflib ratio >= question_min_ratio; scan at most
# max_scroll_steps scrolls down from the top; a heart whose y is below
# low_y_frac * screen height gets one nudge scroll before the tap (the
# compose card opens downward from the tapped heart).
TARGET = {"prompt_min_score": 0.8, "question_min_ratio": 0.9,
          "max_scroll_steps": 7, "low_y_frac": 0.55}

# ---------- Ollama settings (when JUDGE_BACKEND == "ollama") ----------
# Vision-capable models that handle multiple images per turn:
#   "qwen2.5-vl"        — strong all-around vision model (recommended)
#   "qwen2.5-vl:7b"     — smaller, faster, weaker
#   "llama3.2-vision"   — alternative; tool-calling can be flakier
OLLAMA_MODEL = "qwen2.5-vl"

# OLLAMA_HOST: None or "" -> default http://localhost:11434
#              "https://ollama.com" -> Ollama Cloud (requires OLLAMA_API_KEY)
# Can also be set via the OLLAMA_HOST environment variable.
OLLAMA_HOST = None

# ---------- Paths ----------
BASE_DIR = Path(__file__).parent
DEBUG_DIR = BASE_DIR / "debug"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
RUNS_DIR = DEBUG_DIR / "runs"  # per-run logs (runlog.py); kept permanently
SAVE_DEBUG_FRAMES = True  # keep frames + decisions in debug/ for review
# debug/openers.html is rewritten after every like; an open tab reloads
# every this many seconds (0 = no reload tag).
OPENERLOG_HTML_REFRESH = 30

# Pre-like identity check: re-read a missing "Skip <name>" label this many
# times, waiting this many seconds first, before halting the run.
IDENTITY_RETRIES = 2
IDENTITY_RETRY_WAIT = 4


def _apply_mode() -> None:
    """Resolve ACTIVE_MODE and populate this module's PREFERENCES /
    AGE_MIN / AGE_MAX / MESSAGE_VOICE / MODE_NAME / cap overrides.

    Re-entrant — main.py calls this again after parsing --mode so a CLI
    override takes effect before the judge sees config.
    """
    import modes
    mode = modes.load(ACTIVE_MODE)
    g = globals()
    g["PREFERENCES"] = mode.PREFERENCES
    g["AGE_MIN"] = getattr(mode, "AGE_MIN", None)
    g["AGE_MAX"] = getattr(mode, "AGE_MAX", None)
    g["MESSAGE_VOICE"] = getattr(mode, "MESSAGE_VOICE", None)
    g["MODE_NAME"] = mode.NAME
    g["PREMADES"] = list(getattr(mode, "PREMADES", []))
    for k in ("MAX_LIKES_PER_SESSION", "MAX_PROFILES_PER_SESSION"):
        v = getattr(mode, k, None)
        if v is not None:
            g[k] = v


def load_device_json(path: Path, calibrate_hint: str) -> dict:
    """Load a per-device coords JSON, refusing one calibrated for a
    different screen (e.g. emulator coords while DEVICE is the phone)."""
    import json
    if not path.exists():
        raise RuntimeError(f"{path.name} not found. Run `{calibrate_hint}` first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    want = [SCREEN_WIDTH, SCREEN_HEIGHT]
    got = list(data.get("screen_size") or [])
    if got != want:
        raise RuntimeError(
            f"{path.name} was calibrated for {got[0] if got else '?'}x"
            f"{got[1] if len(got) > 1 else '?'}, current device is "
            f"{want[0]}x{want[1]} — recalibrate (`{calibrate_hint}`)."
        )
    return data


def _apply_device() -> None:
    """Overlay the DEVICE profile onto ADB_SERIAL / SCREEN_* / COORDS."""
    profile = DEVICES[DEVICE]
    g = globals()
    g["ADB_SERIAL"] = profile.get("serial")
    g["SKIP_FINGERPRINT"] = profile.get("skip_fingerprint")
    if "screen" in profile:
        g["SCREEN_WIDTH"], g["SCREEN_HEIGHT"] = profile["screen"]
    COORDS.update(profile.get("coords", {}))
    COMPOSE.update(profile.get("compose", {}))
    FILTER_CAL.update(profile.get("filter_cal", {}))
    g["SELF_VIEW_BACK_TAPS"] = profile.get("self_view_back_taps", 2)


_apply_mode()
_apply_device()
