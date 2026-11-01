"""HingeAuto orchestrator.

Loop: capture profile frames -> ask Claude -> tap like or skip -> repeat.
"""

import argparse
import hashlib
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import io

from PIL import Image

import adb
import config
import metrics
import runlog
import ui
import vision
from judge_common import load_backend

judge = load_backend().judge


def _profile_region_hash(png: bytes) -> str:
    """md5 over a cropped region of the frame, excluding status bar (clock
    ticks every minute) and bottom nav (badges flicker). Two screenshots
    of the same profile taken 60+ seconds apart should produce the same
    hash; two different profiles should always differ."""
    img = Image.open(io.BytesIO(png))
    w, h = img.size
    crop = img.crop((0, 120, w, h - 220))
    return hashlib.md5(crop.tobytes()).hexdigest()


def capture_profile() -> list[bytes]:
    """Scroll through the current profile, returning a list of PNG frames."""
    # Defensive: new profiles load at the top, so this is just guarding
    # against the app being mid-scroll from a prior partial action. A
    # handful of swipes is enough — full 18-swipe sweep isn't needed
    # because we aren't recovering from a 7-frame scroll-down.
    scroll_back_to_top(swipes=5)

    frames = []
    frames.append(adb.screenshot())
    adb.jitter_sleep("after_screenshot")

    for _ in range(config.FRAMES_PER_PROFILE - 1):
        adb.scroll_down()
        adb.jitter_sleep("after_scroll")
        frames.append(adb.screenshot())
        adb.jitter_sleep("after_screenshot")

    return frames


def scroll_back_to_top(swipes: int | None = None) -> None:
    """Scroll back to the top of the profile.

    Hinge uses momentum scrolling; each swipe's actual travel is much less
    than the gesture's pixel distance. Default `swipes` is `FRAMES_PER_PROFILE
    * 2 + 4` (18 with FRAMES=7) — enough to recover from a full scroll-down
    through the profile. Pass a smaller `swipes` for defensive top-scrolls
    where the profile is already at or near the top.
    """
    if swipes is None:
        swipes = config.FRAMES_PER_PROFILE * 2 + 4
    for _ in range(swipes):
        adb.scroll_up()
        adb.jitter_sleep("after_scroll")


def do_skip() -> None:
    """Tap the X to advance to the next profile. Always taps (even in dry run);
    advancing is needed for the loop to see new profiles."""
    x, y = config.COORDS["skip_button"]
    adb.tap(x, y)
    adb.jitter_sleep("after_skip")


class LikeAborted(RuntimeError):
    """The like was abandoned before Send Like was tapped; nothing went out.
    `outcome` is like_aborted_detection or like_aborted_text."""

    def __init__(self, outcome: str, msg: str):
        super().__init__(msg)
        self.outcome = outcome


class LikeUnconfirmed(RuntimeError):
    """Send Like was tapped but Hinge didn't confirm it. Screen state is
    unknown, so the loop halts rather than tapping anything else."""

    outcome = "like_unconfirmed"


def recover_from_failed_like() -> None:
    """A like attempt died mid-flow (possibly with the compose keyboard up,
    covering the skip X). Hide the keyboard if it's up, then skip."""
    adb.hide_keyboard()
    do_skip()


def do_like(message: str = "") -> str:
    """In live mode: scroll to top, tap heart, type message (if any), tap Send Like.
    In dry run: advance by skipping (so we never send an actual like).

    Send Like / comment input positions are found at tap-time via vision —
    the compose card anchors to whichever heart was tapped and shifts per
    profile, so static COORDS don't survive across profiles.
    """
    if config.DRY_RUN:
        do_skip()
        return "skipped"
    # Scroll back to the top before tapping a heart. The compose box
    # anchors to the tapped element and extends DOWNWARD — if we tap
    # a heart that's already low on screen (which it is after capture),
    # Send Like ends up off-screen and undetectable. Worth the ~14s.
    scroll_back_to_top()
    top = adb.screenshot()
    before_hash = _profile_region_hash(top)
    heart_xy = vision.find_first_heart(top)
    runlog.event("detect", target="heart", result=heart_xy)
    if heart_xy is None:
        # Static fallback used to fire here, but it silently misses on
        # profiles where the heart's real position differs from the
        # calibrated coord (different layouts, partial scroll-back). The
        # loop would then type/tap into the void and never advance,
        # producing an infinite-loop on the same profile. Bail to skip
        # instead so the profile advances and the loop survives.
        raise LikeAborted("like_aborted_detection",
                          "vision: couldn't find photo-1 heart after scroll-back")
    adb.tap(*heart_xy, target="heart")
    adb.jitter_sleep("after_tap")

    send_xy = vision.find_send_like(adb.screenshot())
    runlog.event("detect", target="send_like", result=send_xy)
    if send_xy is None:
        # Same reasoning as the heart fallback above — silent fallback
        # masks a real failure and traps the loop. Skip instead.
        raise LikeAborted("like_aborted_detection",
                          "vision: couldn't find Send Like after heart tap")
    comment_xy = vision.find_comment_input(send_xy)

    if message:
        adb.tap(*comment_xy, target="comment_input")
        adb.jitter_sleep("after_tap")
        # Snapshot empty-field text-pixel baseline so we can detect when
        # the typed text has actually landed in the EditText buffer.
        empty_pixels = vision.comment_field_text_pixels(adb.screenshot(), send_xy)
        adb.input_text(message)
        # Poll for the field to fill. Under host CPU contention (e.g. a
        # game running alongside the emulator) `input text` events can
        # dispatch slower than expected — short fixed waits drop chars.
        # Expected pixel count grows with message length; require we see
        # well above the empty baseline before sending.
        deadline = time.monotonic() + 15
        target_pixels = empty_pixels + max(150, 20 * len(message))
        while time.monotonic() < deadline:
            time.sleep(1.0)
            current = vision.comment_field_text_pixels(adb.screenshot(), send_xy)
            if current >= target_pixels:
                break
        else:
            # Don't send a truncated / empty opener. Raising lands in the
            # loop's do_like recovery (skip), so nothing goes out.
            raise LikeAborted(
                "like_aborted_text",
                f"typed text didn't land (text pixels {current}, want "
                f"{target_pixels}) — not sending")
        # Typed text can wrap to multiple lines, expanding the comment
        # field and pushing Send Like down. Re-find against the post-type
        # screen so the tap lands on the actual button position.
        post_type_xy = vision.find_send_like(adb.screenshot())
        if post_type_xy is not None:
            send_xy = post_type_xy
    adb.tap(*send_xy, target="send_like")
    adb.jitter_sleep("after_like_sent")
    return _confirm_like_sent(before_hash)


def _read_screen_labels() -> list:
    try:
        return ui.ui_texts()
    except ui.UiDumpFailed as e:
        raise LikeUnconfirmed(f"screen unknown after send: {e}") from e


def _confirm_like_sent(before_hash: str) -> str:
    """After Send Like: handle Hinge's "Send a Rose instead?" upsell sheet,
    then require that the compose card is gone and the profile changed.

    Returns liked_confirmed or like_sheet_handled; raises LikeUnconfirmed
    otherwise. Sheet buttons are only tapped via ui.safe_tap_label, which
    refuses any point on a Rose element (ui.UnsafeTap propagates).
    """
    outcome = "liked_confirmed"
    nodes = _read_screen_labels()
    if ui.find_label(nodes, "Send Like anyway"):
        runlog.event("sheet", target="rose_upsell_sheet")
        ui.safe_tap_label(nodes, "Send Like anyway", target="send_like_anyway")
        adb.jitter_sleep("after_like_sent")
        nodes = _read_screen_labels()
        if ui.find_label(nodes, "Send Like anyway"):
            raise LikeUnconfirmed("rose sheet persisted after 'Send Like anyway'")
        outcome = "like_sheet_handled"
    if ui.find_label(nodes, "Close sheet"):
        labels = [n.label for n in nodes][:10]
        raise LikeUnconfirmed(f"unknown sheet after send: {labels}")

    after = adb.screenshot()
    if vision.find_send_like(after) is not None:
        raise LikeUnconfirmed("Send Like still visible after send")
    if _profile_region_hash(after) == before_hash:
        raise LikeUnconfirmed("profile did not advance after send")
    return outcome


def save_debug(frames: list[bytes], decision, profile_idx: int) -> None:
    if not config.SAVE_DEBUG_FRAMES:
        return
    bucket = "liked" if decision.decision == "like" else "skipped"
    bucket_dir = config.DEBUG_DIR / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-z0-9]", "", (decision.name or "unknown").lower()) or "unknown"
    folder = bucket_dir / f"{profile_idx:02d}_{safe_name}"
    imgs = folder / "imgs"
    imgs.mkdir(parents=True, exist_ok=True)
    for i, png in enumerate(frames):
        (imgs / f"frame_{i:02d}.png").write_bytes(png)
    (folder / "decision.txt").write_text(
        f"name: {decision.name}\n"
        f"decision: {decision.decision}\n"
        f"confidence: {decision.confidence}\n"
        f"reasoning: {decision.reasoning}\n"
        f"message: {decision.message}\n"
        f"message_archetype: {decision.message_archetype}\n"
        f"prompt_referenced: {decision.prompt_referenced}\n"
        f"skip_reason: {decision.skip_reason}\n"
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}\n"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HingeAuto loop runner")
    p.add_argument(
        "--mode",
        default=None,
        help="Override config.ACTIVE_MODE for this run (one-shot). "
             "Must match a file under modes/<name>.py.",
    )
    p.add_argument(
        "--set-filters",
        action="store_true",
        help="Before looping, drive the in-app age slider to match the "
             "active mode's AGE_MIN/AGE_MAX. Requires filter_coords.json "
             "(see calibrate_filters.py).",
    )
    p.add_argument(
        "--location",
        default=None,
        help="Before looping, change Hinge's 'My neighborhood' to the "
             "named city (resolved via locations.json). Orthogonal to "
             "--mode. Requires location_coords.json + Hinge+/X for "
             "out-of-area changes.",
    )
    p.add_argument(
        "--rotate",
        default=None,
        help="Named rotation path from locations.json _rotations (e.g. "
             "'atl'). When Hinge's 'You've seen everyone' screen appears, "
             "advance to the next city in the rotation. Loops back to "
             "start when exhausted.",
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args()
    load_dotenv()

    if args.mode:
        config.ACTIVE_MODE = args.mode
        config._apply_mode()

    serial = adb.check_device()
    print(f"Connected to: {serial}")
    age_band = (
        f"age {config.AGE_MIN}-{config.AGE_MAX}"
        if (config.AGE_MIN is not None or config.AGE_MAX is not None)
        else "no age gate"
    )
    print(f"Mode:     {config.MODE_NAME} ({age_band})")
    print(f"Run:      {'DRY RUN (no taps)' if config.DRY_RUN else 'LIVE (will tap)'}")
    print(f"Max likes: {config.MAX_LIKES_PER_SESSION}, "
          f"max profiles: {config.MAX_PROFILES_PER_SESSION}")

    if args.set_filters:
        if config.AGE_MIN is None and config.AGE_MAX is None:
            print("--set-filters requested but active mode has no age range; "
                  "skipping in-app filter step.")
        else:
            import filters
            print(f"Setting in-app age filter to {config.AGE_MIN}-{config.AGE_MAX}...")
            filters.set_age_range(config.AGE_MIN, config.AGE_MAX)

    if args.location:
        import locations
        resolved = locations.resolve(args.location)
        print(f"Setting location to '{args.location}' (search: '{resolved}')...")
        locations.set_location(args.location)

    rotation_list: list[str] | None = None
    rotation_idx: int = 0
    if args.rotate:
        import locations
        rotation_list = locations.get_rotation(args.rotate)
        # If --location was also set, start the rotation at that location if
        # it's in the list; otherwise prepend it (don't replace the rotation).
        if args.location and args.location in rotation_list:
            rotation_idx = rotation_list.index(args.location)
        print(f"Rotation '{args.rotate}': {rotation_list} (starting at "
              f"index {rotation_idx} = '{rotation_list[rotation_idx]}')")

    print("Starting in 5s. Make sure Hinge is open on the Discover tab.")
    time.sleep(5)

    likes_sent = 0
    skips = 0
    profiles_seen = 0
    total_cost = 0.0
    total_seconds = 0.0
    last_frame0_hash: str | None = None
    duplicate_streak = 0

    while profiles_seen < config.MAX_PROFILES_PER_SESSION:
        profiles_seen += 1
        print(f"\n--- Profile {profiles_seen} ---")

        t0 = time.monotonic()
        frames = capture_profile()
        t_capture = time.monotonic() - t0
        print(f"Captured {len(frames)} frames")

        # Out-of-candidates detection: if frame 0 shows the "You've seen
        # everyone for now" screen, we're stuck. If --rotate is on, advance
        # to the next city; otherwise just break since further iteration
        # would just force-skip nothing.
        if rotation_list is not None:
            import locations
            if locations.is_out_of_candidates(frames[0]):
                rotation_idx = (rotation_idx + 1) % len(rotation_list)
                next_city = rotation_list[rotation_idx]
                print(f"OUT OF CANDIDATES — rotating to '{next_city}' "
                      f"(index {rotation_idx}/{len(rotation_list) - 1}).")
                try:
                    locations.set_location(next_city)
                except Exception as e:
                    print(f"set_location failed: {e!r} — continuing in place.")
                last_frame0_hash = None
                duplicate_streak = 0
                profiles_seen -= 1  # don't count the empty-state capture
                continue

        # If frame 0 is identical to the previous profile's frame 0, Hinge
        # didn't advance after our last action — force-skip rather than
        # burning another ~$0.035 re-judging the same person. Escalate the
        # delay if we keep duplicating, in case Hinge needs a beat to
        # recover from an "out of likes" / popup state.
        #
        # Hash a cropped region of frame 0 (excluding status bar at top and
        # nav bar at bottom). The status-bar clock ticks every minute and
        # the nav-bar can show transient badges; both make raw-bytes md5
        # diverge across iterations even when the *profile* is identical,
        # which silently breaks duplicate detection. Cropping isolates the
        # part of the screen that actually identifies the profile.
        frame0_hash = _profile_region_hash(frames[0])
        if frame0_hash == last_frame0_hash:
            duplicate_streak += 1
            print(f"DUPLICATE: frame 0 matches previous profile "
                  f"(streak {duplicate_streak}). Force-skipping without judge.")
            try:
                do_skip()
            except Exception as e:
                print(f"Skip failed during duplicate recovery: {e!r}")
            time.sleep(min(2 + duplicate_streak * 2, 15))
            continue
        last_frame0_hash = frame0_hash
        duplicate_streak = 0

        t1 = time.monotonic()
        decision = None
        fatal_error = None
        for attempt in range(3):
            try:
                decision = judge(frames)
                break
            except Exception as e:
                err = repr(e)
                print(f"Judge attempt {attempt + 1}/3 failed: {e}")
                # Halt on errors that won't recover with a retry — burning
                # through Hinge swipes blind (force-skipping every profile
                # without a real decision) eats the daily quota and looks
                # robotic to Hinge. Saw this once when the Anthropic credit
                # balance hit zero mid-run: 124 profiles got blindly skipped
                # before we noticed.
                if any(s in err for s in (
                    "credit balance is too low",
                    "authentication_error",
                    "invalid_api_key",
                    "permission_error",
                )):
                    fatal_error = err
                    break
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
        if fatal_error is not None:
            print(f"\nFATAL judge error — halting loop instead of burning "
                  f"Hinge swipes:\n  {fatal_error}")
            break
        t_judge = time.monotonic() - t1
        if decision is None:
            print("Judge failed 3 times — skipping this profile to keep the loop alive.")
            do_skip()
            continue

        print(f"Name:     {decision.name}")
        print(f"Decision: {decision.decision} ({decision.confidence}) "
              f"[{decision.skip_reason if decision.decision == 'skip' else decision.message_archetype}]")
        print(f"Reason:   {decision.reasoning}")
        if decision.message:
            print(f"Message:  {decision.message}")
        save_debug(frames, decision, profiles_seen)

        t2 = time.monotonic()
        if decision.decision == "like":
            if likes_sent >= config.MAX_LIKES_PER_SESSION:
                print(f"Hit max likes cap ({config.MAX_LIKES_PER_SESSION}). Stopping.")
                break
            try:
                do_like(decision.message)
                likes_sent += 1
            except Exception as e:
                print(f"do_like failed: {e!r} — recovering by skipping this profile.")
                try:
                    recover_from_failed_like()
                except Exception as e2:
                    print(f"do_skip recovery also failed: {e2!r} — loop will retry next iter.")
                skips += 1
        else:
            do_skip()
            skips += 1
        t_act = time.monotonic() - t2

        timing = {
            "capture_seconds": round(t_capture, 2),
            "judge_seconds":   round(t_judge, 2),
            "act_seconds":     round(t_act, 2),
            "total_seconds":   round(t_capture + t_judge + t_act, 2),
        }
        metrics.log_profile(profiles_seen, decision, timing)
        total_cost += metrics.estimated_cost(decision.usage)
        total_seconds += timing["total_seconds"]
        metrics.print_running_totals(
            profiles_seen, likes_sent, skips, total_cost, total_seconds,
        )

    print(f"\nDone. {likes_sent} likes sent across {profiles_seen} profiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
