"""Configuration for HingeAuto.

PREFERENCES, AGE_MIN/MAX, and MESSAGE_VOICE come from the active mode (see
`modes/`). Set ACTIVE_MODE here for the persistent default; override per-run
via `python main.py --mode <name>`.

The COORDS defaults below are PLACEHOLDERS for a Pixel 10 emulator at
1080x2424. They WILL be wrong for your device — run `python calibrate.py`
before your first session.
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
# DRY_RUN = True  -> decide and log, but force-skip every profile instead
#                    of liking. Note: this burns through your Discover
#                    queue — every "would-like" profile gets skipped and
#                    is gone. Most users should leave this False and just
#                    start with a small MAX_LIKES_PER_SESSION while they
#                    tune their rubric.
DRY_RUN = False

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

# ---------- Coordinates (CALIBRATE BEFORE USE) ----------
# All values in absolute pixels for the configured screen size.
# These are PLACEHOLDERS based on one emulator setup — they will not match
# yours. Run `python calibrate.py` and overwrite these.
COORDS = {
    # Skip / like action targets
    "skip_button":       (140, 2068),
    "heart_photo_1":     (938, 1426),

    # Compose box (anchors to the element whose heart was tapped; these
    # values assume the heart on photo 1 was tapped — vision.py re-finds
    # them at tap-time, so these are mostly fallbacks).
    "send_like_button":  (687, 1488),
    "comment_input":     (540, 1317),
    "compose_close":     (960, 200),

    # Scroll gesture (swipe up = scroll down through profile).
    "scroll_from":       (540, 1700),
    "scroll_to":         (540, 700),
    "scroll_duration_ms": 350,
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
SAVE_DEBUG_FRAMES = True  # keep frames + decisions in debug/ for review


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


_apply_mode()
