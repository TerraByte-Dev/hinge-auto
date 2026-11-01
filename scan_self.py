"""Capture your own Hinge profile and ask Claude what to improve.

This is the *non-ToS-violating* feature in this repo. It taps through to
your own profile's "View" tab (what other people see), scrolls top to
bottom capturing frames, sends them to Claude, and writes a Markdown
report with specific suggestions for photos, prompts, and overall hook
strength.

Flow:
  1. From Discover, tap the pfp icon in the bottom-right nav.
  2. Tap your own avatar at the top of the profile tab.
  3. Tap the "View" tab to see the profile-as-others-see-it layout.
  4. Scroll top-to-bottom capturing N frames.
  5. Tap back-arrow twice, then the Discover tab to return.
  6. Send the frames to Claude with a profile-coach prompt.
  7. Save the report to `debug/self_scan_<timestamp>.md`.

Usage:
  python scan_self.py             # full pipeline + write report
  python scan_self.py --frames 5  # fewer scroll passes for shorter
                                  # profiles
  python scan_self.py --no-return # leave the app on the View screen
                                  # (useful for debugging)
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import adb
import config


DEFAULT_FRAMES = 7


SYSTEM_PROMPT = """You are an expert Hinge profile coach reviewing the
user's own profile. You will be shown a sequence of screenshots
representing the profile in top-to-bottom scroll order — the same view
other people see when they open the profile on Hinge.

Your job is to give SPECIFIC, ACTIONABLE feedback the user can implement
this week. No generic advice. No "be yourself" energy.

Cover, in order:

## Photos
For each photo (1, 2, 3, ...), state what it's doing well and what's
weakening it. Reference what's visible — the setting, the activity, the
expression, what the photo is communicating. Call out:
- Photos that look like duplicates (same outfit / same setting / same
  expression as another photo) — they waste a slot.
- Photos where the user isn't clearly visible (blurry, too far away,
  group shot where it's unclear which person is them).
- Photos that don't add a new dimension to the profile (every photo
  showing the same hobby, every photo indoors, etc.).
- The best photo, and a recommendation for which one should be photo 1.

## Prompts
For each prompt answer, evaluate it as a conversation hook. Strong
prompts give the other person something specific and easy to ask about.
Call out:
- Prompts that are too generic ("I'm a sucker for: dogs and pizza" —
  nothing to grab onto).
- Prompts that contradict each other or repeat the same theme.
- Specific rewrites for the weakest prompt, in the user's apparent voice.

## Overall hook strength
What is the strongest single thing about this profile someone could
reference in an opener? What is the weakest part that's costing matches?
What's the ONE change you'd make first?

Be direct. Use plain ASCII. No emojis. Markdown headers OK."""


# Label that must be on screen after this flow's first navigation tap
# (ui.require_label). Read from the phone with ui.ui_texts(); None =
# not captured yet, check skipped.
VIEW_LABEL: str | None = "View"


def navigate_to_self_view() -> None:
    """Discover -> self-pfp -> avatar -> View tab. Assumes Hinge is
    open on Discover when called."""
    c = config.COORDS
    adb.tap(*c["nav_self_pfp"])
    adb.jitter_sleep("after_tap")
    time.sleep(1.0)
    adb.tap(*c["self_avatar"])
    adb.jitter_sleep("after_tap")
    time.sleep(1.0)
    import ui
    ui.require_label(VIEW_LABEL, step="scan_self: profile editor")
    adb.tap(*c["view_tab"])
    adb.jitter_sleep("after_tap")
    time.sleep(1.0)


def scroll_to_top(swipes: int = 8) -> None:
    """Scroll up enough to guarantee we're at the top of the view."""
    for _ in range(swipes):
        adb.scroll_up()
        adb.jitter_sleep("after_scroll")


def capture_self_frames(n_frames: int = DEFAULT_FRAMES) -> list[bytes]:
    """Top-to-bottom scroll capture of the View tab."""
    scroll_to_top()
    frames = [adb.screenshot()]
    adb.jitter_sleep("after_screenshot")
    for _ in range(n_frames - 1):
        adb.scroll_down()
        adb.jitter_sleep("after_scroll")
        frames.append(adb.screenshot())
        adb.jitter_sleep("after_screenshot")
    return frames


def return_to_discover() -> None:
    """Back-arrow (SELF_VIEW_BACK_TAPS times: 2 on the emulator, 1 on the
    S23 where one Back from View lands on Profile Hub), then Discover."""
    c = config.COORDS
    for _ in range(config.SELF_VIEW_BACK_TAPS):
        adb.tap(*c["back_arrow"], target="back_arrow")
        time.sleep(0.8)
    adb.tap(*c["nav_discover"], target="nav_discover")
    time.sleep(1.0)


def review(frames: list[bytes]) -> str:
    """Send frames to Claude and return the markdown review."""
    import anthropic

    client = anthropic.Anthropic()

    def img_block(png: bytes) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(png).decode("utf-8"),
            },
        }

    content = [img_block(f) for f in frames]
    content.append({
        "type": "text",
        "text": (
            f"Above are {len(frames)} screenshots of my Hinge profile in "
            "top-to-bottom scroll order. Give me your full coach review."
        ),
    })

    response = client.messages.create(
        model=config.MODEL,
        max_tokens=4000,
        output_config={"effort": "high"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                        help=f"Frames to capture (default {DEFAULT_FRAMES}).")
    parser.add_argument("--no-return", action="store_true",
                        help="Leave the app on the View screen.")
    parser.add_argument("--save-frames", action="store_true",
                        help="Save the captured PNGs to debug/ alongside "
                             "the report.")
    args = parser.parse_args()

    load_dotenv()
    adb.check_device()

    print("[self-scan] Navigating to View tab...")
    navigate_to_self_view()

    print(f"[self-scan] Capturing {args.frames} frames...")
    frames = capture_self_frames(args.frames)

    if not args.no_return:
        print("[self-scan] Returning to Discover...")
        return_to_discover()

    print("[self-scan] Sending to Claude for review...")
    report = review(frames)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = config.DEBUG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.save_frames:
        frame_dir = out_dir / f"self_scan_{stamp}_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        for i, png in enumerate(frames):
            (frame_dir / f"frame_{i:02d}.png").write_bytes(png)
        print(f"[self-scan] Saved frames to {frame_dir}")

    report_path = out_dir / f"self_scan_{stamp}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n[self-scan] Report written to {report_path}\n")
    print("=" * 60)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
