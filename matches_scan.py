"""Scrape the Hinge Matches tab and write structured records.

Runs in two phases:

  1. **Capture** — tap the Matches tab, screenshot, scroll & screenshot
     a few more times. Frames are stitched-by-vision, not by pixels.

  2. **Extract** — Claude vision reads the frames and emits a list of
     `{name, status, last_seen, has_unread}` rows via a tool call.

Output is appended to `debug/matches_log.jsonl`, one JSON line per scan
with an array of extracted rows plus scan-level metadata. The analytics
side (HingeAnalytics/build.py) joins on lowercased `name` against the
session log to compute match-rate-by-archetype, conversion funnel, etc.

Usage:
  python matches_scan.py                # one scan, writes JSONL line
  python matches_scan.py --dry-run      # capture + extract, print only
  python matches_scan.py --frames 5     # extra scroll passes
  python -c "import matches_scan; matches_scan.scan_and_log()"

Needs `matches_coords.json` (run `python calibrate_matches.py` first).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import numpy as np
from dotenv import load_dotenv

import adb
import config

load_dotenv()


COORDS_PATH = config.BASE_DIR / "matches_coords.json"

# Label that must be on screen after this flow's first navigation tap
# (ui.require_label). Read from the phone with ui.ui_texts(); None =
# not captured yet, check skipped.
# "Your turn"/"Their turn" are section headers from the emulator era; not yet
# seen on the S23 (no matches when measured).
MATCHES_LABELS: tuple[str, ...] | None = ("No matches right now", "Your turn", "Their turn")
LOG_PATH = config.DEBUG_DIR / "matches_log.jsonl"
DEFAULT_FRAMES = 4


# ---------- coords ----------


def _load_coords() -> dict:
    try:
        return config.load_device_json(COORDS_PATH, "python calibrate_matches.py")
    except RuntimeError as e:
        raise SystemExit(str(e))


# ---------- capture ----------


def open_matches_tab() -> None:
    """Tap the Matches/Conversations icon in the bottom nav."""
    coords = _load_coords()
    x, y = coords["matches_tab"]
    adb.tap(x, y, target="matches_tab")
    adb.jitter_sleep("after_tap")
    import ui
    ui.require_label(MATCHES_LABELS, step="matches: Matches tab")
    # Hinge's matches view loads progressively (avatars first, then text).
    # An extra short pause makes the first screenshot reliable.
    time.sleep(1.2)


def _scroll_matches() -> None:
    """Swipe up to scroll the matches list by ~400px (less than the
    global discover-feed scroll). A larger scroll in the matches view
    triggers Hinge's section-collapse behavior on the active section,
    so we keep the gesture short."""
    adb.swipe(*config.COORDS["matches_scroll_from"],
              *config.COORDS["matches_scroll_to"], 400)
    adb.jitter_sleep("after_scroll")


def _try_expand_their_turn(frames: list[bytes]) -> bytes | None:
    """Tap to expand the 'Their turn' header. Hinge always re-collapses
    this section when the Matches tab is opened fresh, so we can tap
    unconditionally and trust the toggle goes from collapsed → expanded.

    Returns the post-tap screenshot, or None if frames is empty.

    Per-pixel chevron-orientation detection was tried and proved
    unreliable (collapsed and expanded chevrons differ by only ~12% in
    top/bottom dark-pixel ratio at this resolution, well within the
    noise from row separators).
    """
    if not frames:
        return None
    adb.tap(*config.COORDS["matches_expand_their_turn"])
    adb.jitter_sleep("after_tap")
    time.sleep(0.6)
    return adb.screenshot()


def capture_matches(n_frames: int = DEFAULT_FRAMES, expand: bool = True) -> list[bytes]:
    """Return ordered PNG bytes of the matches view.

    Frame 0 is always the matches view immediately after tab-open. If
    `expand` and 'Their turn' was collapsed, a second frame is captured
    after tapping to expand it. Additional scrolled frames are appended
    up to `n_frames` total — useful when the user has more matches than
    fit on screen, ignored when the list already fits.

    Scrolling AFTER an expand can re-collapse the section on some
    Hinge versions; if you only need names and the list is short,
    n_frames=2 (just open + expand) is the safest call."""
    if n_frames < 1:
        raise ValueError("n_frames must be >= 1")
    open_matches_tab()
    frames: list[bytes] = [adb.screenshot()]
    if expand:
        expanded = _try_expand_their_turn(frames)
        if expanded is not None:
            frames.append(expanded)
    for _ in range(max(0, n_frames - len(frames))):
        _scroll_matches()
        frames.append(adb.screenshot())
    return frames


# ---------- extract via Claude ----------


SYSTEM_PROMPT = """You are reading screenshots of the Hinge Matches tab.

The Hinge Matches tab is organized into two collapsible sections with
counts in their headers:

  - "Your turn (N)"   — matches where the user owes a reply
  - "Their turn (N)"  — matches where the other person owes a reply

Each section, when expanded, lists rows of:
  - circular avatar
  - first name (a single given name like "Alex" or "Sam")
  - a one-line message preview (the most recent message in the convo,
    truncated)

A section may be collapsed (chevron pointing down) — in that case only
its header + count is visible.

Your job is to extract THREE things:

  1. `your_turn_count` — the integer N in "Your turn (N)". -1 if not
     visible on any frame.
  2. `their_turn_count` — the integer N in "Their turn (N)". -1 if not
     visible on any frame.
  3. `matches` — for every match row whose name is clearly readable
     **anywhere across ANY of the frames**, one entry with:
       - name: first name, lowercased ASCII letters only (strip
         spaces, punctuation, emoji). Deduplicate across frames.
       - bucket: "your_turn" | "their_turn" | "unknown"
         based on which section header sits above the row.
       - preview: the visible message-preview text (verbatim, may be
         empty if no preview shown).

**Frames are scrolled views of the SAME list** — early frames show the
top of the list, later frames show further down. A given row may
appear in multiple frames at different y positions; that's the same
person, listed once. Aggregate ACROSS frames; do not restrict to
what one frame shows. If `their_turn_count` says 6 but only 4 names
fit in any single frame, look at later (scrolled) frames for the
remaining 2 — they're almost certainly there.

Be conservative on names: only include people whose name you can
clearly read. Hinge sometimes truncates names; skip those rather
than guess. Do not invent names.

If the screen shows no matches at all (empty state), set both counts
to 0, matches to [], and explain in `notes`.

Submit via the report_matches tool."""


REPORT_TOOL = {
    "name": "report_matches",
    "description": "Submit the parsed match data from the Hinge Matches tab.",
    "input_schema": {
        "type": "object",
        "properties": {
            "your_turn_count": {
                "type": "integer",
                "description": (
                    "Integer N from the 'Your turn (N)' section header. "
                    "Use -1 if the header isn't visible on any frame."
                ),
            },
            "their_turn_count": {
                "type": "integer",
                "description": (
                    "Integer N from the 'Their turn (N)' section header. "
                    "Use -1 if the header isn't visible on any frame."
                ),
            },
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":    {"type": "string"},
                        "bucket":  {
                            "type": "string",
                            "enum": ["your_turn", "their_turn", "unknown"],
                        },
                        "preview": {"type": "string"},
                    },
                    "required": ["name", "bucket", "preview"],
                },
            },
            "notes": {
                "type": "string",
                "description": (
                    "One-line description of what was visible "
                    "(e.g. 'Your turn expanded with 2 rows, Their turn "
                    "collapsed'). Use to explain edge cases."
                ),
            },
        },
        "required": ["your_turn_count", "their_turn_count", "matches", "notes"],
    },
}


@dataclass
class ScanResult:
    your_turn_count: int
    their_turn_count: int
    matches: list[dict]
    notes: str
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def total_matches(self) -> int:
        """Sum of the two section counts. -1 if either is unknown."""
        if self.your_turn_count < 0 or self.their_turn_count < 0:
            return -1
        return self.your_turn_count + self.their_turn_count


def _image_block(png: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png).decode("utf-8"),
        },
    }


def extract_matches(frames: list[bytes]) -> ScanResult:
    """Send frames to Claude, return parsed match list."""
    client = anthropic.Anthropic()

    content = [_image_block(f) for f in frames]
    content.append({
        "type": "text",
        "text": (
            f"Above are {len(frames)} screenshots of the Hinge Matches tab, "
            "in scroll order (top first). Extract everyone visible."
        ),
    })

    response = client.messages.create(
        model=config.MODEL,
        max_tokens=2000,
        output_config={"effort": config.EFFORT},
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        tools=[REPORT_TOOL],
        tool_choice={"type": "tool", "name": "report_matches"},
        messages=[{"role": "user", "content": content}],
    )

    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "cache_creation_input_tokens":
            getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens":
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }

    for block in response.content:
        if block.type == "tool_use" and block.name == "report_matches":
            return ScanResult(
                your_turn_count=block.input.get("your_turn_count", -1),
                their_turn_count=block.input.get("their_turn_count", -1),
                matches=block.input.get("matches", []),
                notes=block.input.get("notes", ""),
                usage=usage,
            )

    raise RuntimeError(
        f"Claude did not return a tool_use block. "
        f"stop_reason={response.stop_reason}"
    )


# ---------- log ----------


def _normalize_name(s: str) -> str:
    """Match the lowercasing/strip applied by judge.py so analytics
    can left-join cleanly."""
    return "".join(c for c in s.strip().lower() if c.isalpha())


def append_log(result: ScanResult, n_frames: int) -> Path:
    """Append one scan to matches_log.jsonl."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "scan_timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": config.MODE_NAME,
        "n_frames": n_frames,
        "your_turn_count": result.your_turn_count,
        "their_turn_count": result.their_turn_count,
        "total_matches": result.total_matches,
        "notes": result.notes,
        "matches": [
            {
                "name": _normalize_name(m["name"]),
                "raw_name": m["name"],
                "bucket": m.get("bucket", "unknown"),
                "preview": m.get("preview", ""),
            }
            for m in result.matches
            if m.get("name")
        ],
        "tokens": result.usage,
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return LOG_PATH


# ---------- orchestration ----------


def scan_and_log(
    n_frames: int = DEFAULT_FRAMES,
    dry_run: bool = False,
    expand: bool = True,
) -> ScanResult:
    """Full pipeline: capture + extract + (optionally) log."""
    adb.check_device()
    print(f"[matches] capturing {n_frames} frames (expand={expand})...")
    t0 = time.time()
    frames = capture_matches(n_frames, expand=expand)
    t1 = time.time()
    print(f"[matches] captured in {t1 - t0:.1f}s — extracting...")
    result = extract_matches(frames)
    t2 = time.time()
    print(
        f"[matches] extracted in {t2 - t1:.1f}s — "
        f"your turn: {result.your_turn_count}, "
        f"their turn: {result.their_turn_count}, "
        f"{len(result.matches)} named rows"
    )
    print(f"[matches] notes: {result.notes}")
    for m in result.matches:
        preview = m.get("preview", "")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        print(f"  - {m['name']:20s}  {m.get('bucket', '?'):12s}  {preview}")
    if dry_run:
        print("[matches] DRY-RUN — not writing to log")
    else:
        path = append_log(result, n_frames=n_frames)
        print(f"[matches] wrote {path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--frames", type=int, default=DEFAULT_FRAMES,
        help=f"Number of scroll-and-screenshot passes (default {DEFAULT_FRAMES}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Capture and extract but don't write to matches_log.jsonl.",
    )
    parser.add_argument(
        "--no-expand", action="store_true",
        help="Skip the heuristic tap-to-expand of the 'Their turn' section.",
    )
    args = parser.parse_args()
    try:
        scan_and_log(
            n_frames=args.frames,
            dry_run=args.dry_run,
            expand=not args.no_expand,
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"[matches] error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
