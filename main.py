"""HingeAuto orchestrator.

Loop: capture profile frames -> ask Claude -> tap like or skip -> repeat.
"""

import argparse
import hashlib
import logging
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
log = logging.getLogger("hinge")


class Halt(RuntimeError):
    """Stop the run (not skip): continuing could burn profiles or tap
    blind. `reason` goes into summary.json's exit_reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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


def do_skip() -> str:
    """Tap the X to advance to the next profile. Always taps (even in dry run);
    advancing is needed for the loop to see new profiles.

    Returns "skipped", or "skip_unconfirmed" when the profile didn't change
    (X moved, a popup ate the tap)."""
    before = _profile_region_hash(adb.screenshot())
    x, y = config.COORDS["skip_button"]
    adb.tap(x, y, target="skip_button")
    adb.jitter_sleep("after_skip")
    if _profile_region_hash(adb.screenshot()) == before:
        return "skip_unconfirmed"
    return "skipped"


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

    runlog.start_run()
    state = {"likes_sent": 0, "skips": 0, "profiles_seen": 0,
             "total_cost": 0.0, "total_seconds": 0.0}
    exit_reason, code = "done", 0
    try:
        _setup_and_loop(args, state)
    except Halt as h:
        exit_reason, code = f"halt: {h.reason}", 1
        log.error(f"HALT: {h.reason}")
    except PreflightFailed as e:
        exit_reason, code = f"preflight: {e}", 2
        log.error(f"Hinge layout looks different ({e}). "
                  "Recalibrate or dismiss popups.")
    except KeyboardInterrupt:
        exit_reason, code = "interrupted", 130
        log.warning("Interrupted.")
    except Exception as e:
        exit_reason, code = f"crash: {e!r}", 1
        log.exception("Crashed")
        runlog.incident("crash", None, _safe_screenshot(), e)
    finally:
        totals = dict(state, total_cost=round(state["total_cost"], 4),
                      total_seconds=round(state["total_seconds"], 1))
        path = runlog.write_summary(exit_reason, totals)
        log.info(f"\nDone ({exit_reason}). {state['likes_sent']} likes sent "
                 f"across {state['profiles_seen']} profiles.")
        report = runlog.flagged_report()
        if report:
            log.info(report)
        log.info(f"Run log: {path.parent if path else '-'}")
        runlog.end_run()
    return code


class PreflightFailed(RuntimeError):
    pass


def skip_patch_counts(png: bytes) -> dict:
    """Dark (max channel < 60) and white (min channel > 235) pixel counts in
    the 120x120 patch centered on COORDS["skip_button"]."""
    import numpy as np
    arr = np.array(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    x, y = config.COORDS["skip_button"]
    patch = arr[max(0, y - 60):y + 60, max(0, x - 60):x + 60]
    return {"dark": int((patch.max(axis=-1) < 60).sum()),
            "white": int((patch.min(axis=-1) > 235).sum())}


def preflight(png: bytes, nodes: list) -> list[str]:
    """Reasons the current screen doesn't look like Hinge Discover with the
    calibrated layout. Empty list = OK to start."""
    reasons = []
    fp = config.SKIP_FINGERPRINT
    if fp:
        counts = skip_patch_counts(png)
        for k, want in fp.items():
            if abs(counts[k] - want) > 0.10 * want:
                reasons.append(f"skip X not where expected ({k} pixels "
                               f"{counts[k]}, calibrated {want})")
    if vision.find_first_heart(png) is None:
        reasons.append("no photo heart found on screen")
    if ui.find_label(nodes, "Close sheet"):
        reasons.append("a sheet/popup is open")
    return reasons


def _run_preflight() -> None:
    png = adb.screenshot()
    try:
        nodes = ui.ui_texts()
    except ui.UiDumpFailed as e:
        raise PreflightFailed(f"screen labels unreadable: {e}") from e
    reasons = preflight(png, nodes)
    runlog.event("preflight", result="ok" if not reasons else "fail", detail=reasons)
    if reasons:
        runlog.incident("preflight", None, png)
        raise PreflightFailed("; ".join(reasons))


def _safe_screenshot() -> bytes | None:
    try:
        return adb.screenshot()
    except Exception:
        return None


def _flag(profile: int, outcome: str, before: bytes | None,
          error: BaseException | None = None) -> Path | None:
    """Record an outcome; snapshot an incident if it's a flagged one."""
    incident = None
    if outcome not in runlog.FLAGGED_OK:
        incident = runlog.incident(outcome, before, _safe_screenshot(), error)
        log.warning(f"FLAGGED {outcome}" + (f": {error}" if error else ""))
    runlog.record_outcome(profile, outcome, incident)
    return incident


def _setup_and_loop(args, state: dict) -> None:
    serial = adb.check_device()
    log.info(f"Connected to: {serial}")
    age_band = (
        f"age {config.AGE_MIN}-{config.AGE_MAX}"
        if (config.AGE_MIN is not None or config.AGE_MAX is not None)
        else "no age gate"
    )
    log.info(f"Mode:     {config.MODE_NAME} ({age_band})")
    log.info(f"Run:      {'DRY RUN (skips only, sends nothing)' if config.DRY_RUN else 'LIVE (will tap)'}")
    log.info(f"Max likes: {config.MAX_LIKES_PER_SESSION}, "
             f"max profiles: {config.MAX_PROFILES_PER_SESSION}")
    log.info(f"Run id:   {runlog.run_id()}")

    if args.set_filters:
        if config.AGE_MIN is None and config.AGE_MAX is None:
            log.info("--set-filters requested but active mode has no age range; "
                     "skipping in-app filter step.")
        else:
            import filters
            log.info(f"Setting in-app age filter to {config.AGE_MIN}-{config.AGE_MAX}...")
            filters.set_age_range(config.AGE_MIN, config.AGE_MAX)

    if args.location:
        import locations
        resolved = locations.resolve(args.location)
        log.info(f"Setting location to '{args.location}' (search: '{resolved}')...")
        locations.set_location(args.location)

    rotation_list: list[str] | None = None
    rotation_idx: int = 0
    if args.rotate:
        import locations
        if config.DEVICE != "emulator" and locations.EMPTY_STATE_LABEL is None:
            raise PreflightFailed(
                "--rotate needs locations.EMPTY_STATE_LABEL captured from the "
                "phone's 'seen everyone' screen")
        rotation_list = locations.get_rotation(args.rotate)
        # If --location was also set, start the rotation at that location if
        # it's in the list; otherwise prepend it (don't replace the rotation).
        if args.location and args.location in rotation_list:
            rotation_idx = rotation_list.index(args.location)
        log.info(f"Rotation '{args.rotate}': {rotation_list} (starting at "
                 f"index {rotation_idx} = '{rotation_list[rotation_idx]}')")

    log.info("Starting in 5s. Make sure Hinge is open on the Discover tab.")
    time.sleep(5)
    _run_preflight()

    last_frame0_hash: str | None = None
    duplicate_streak = 0
    detection_streak = 0

    while state["profiles_seen"] < config.MAX_PROFILES_PER_SESSION:
        state["profiles_seen"] += 1
        n = state["profiles_seen"]
        runlog.set_profile(n)
        log.info(f"\n--- Profile {n} ---")

        t0 = time.monotonic()
        frames = capture_profile()
        t_capture = time.monotonic() - t0
        log.info(f"Captured {len(frames)} frames")

        # Out-of-candidates detection: if frame 0 shows the "You've seen
        # everyone for now" screen, we're stuck. If --rotate is on, advance
        # to the next city; otherwise just break since further iteration
        # would just force-skip nothing.
        if rotation_list is not None:
            import locations
            if locations.is_out_of_candidates(frames[0]):
                rotation_idx = (rotation_idx + 1) % len(rotation_list)
                next_city = rotation_list[rotation_idx]
                log.info(f"OUT OF CANDIDATES — rotating to '{next_city}' "
                         f"(index {rotation_idx}/{len(rotation_list) - 1}).")
                try:
                    locations.set_location(next_city)
                except Exception as e:
                    log.warning(f"set_location failed: {e!r} — continuing in place.")
                last_frame0_hash = None
                duplicate_streak = 0
                state["profiles_seen"] -= 1  # don't count the empty-state capture
                continue

        # If frame 0 is identical to the previous profile's frame 0, Hinge
        # didn't advance after our last action — force-skip rather than
        # burning another ~$0.035 re-judging the same person. Three in a
        # row means something is stuck (popup, moved X): halt.
        #
        # Hash a cropped region of frame 0 (excluding status bar at top and
        # nav bar at bottom) so clock ticks and nav badges don't make the
        # same profile look new.
        frame0_hash = _profile_region_hash(frames[0])
        if frame0_hash == last_frame0_hash:
            duplicate_streak += 1
            log.warning(f"DUPLICATE: frame 0 matches previous profile "
                        f"(streak {duplicate_streak}).")
            _flag(n, "duplicate", frames[0])
            if duplicate_streak >= 3:
                raise Halt("3 duplicate frames in a row (Hinge not advancing)")
            try:
                do_skip()
            except Exception as e:
                log.warning(f"Skip failed during duplicate recovery: {e!r}")
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
                log.warning(f"Judge attempt {attempt + 1}/3 failed: {e}")
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
            raise Halt(f"fatal judge error: {fatal_error}")
        t_judge = time.monotonic() - t1
        if decision is None:
            log.warning("Judge failed 3 times — skipping this profile to keep the loop alive.")
            _flag(n, "judge_failed", frames[0])
            do_skip()
            continue

        log.info(f"Name:     {decision.name}")
        log.info(f"Decision: {decision.decision} ({decision.confidence}) "
                 f"[{decision.skip_reason if decision.decision == 'skip' else decision.message_archetype}]")
        log.info(f"Reason:   {decision.reasoning}")
        if decision.message:
            log.info(f"Message:  {decision.message}")
        save_debug(frames, decision, n)

        t2 = time.monotonic()
        if decision.decision == "like":
            if state["likes_sent"] >= config.MAX_LIKES_PER_SESSION:
                log.info(f"Hit max likes cap ({config.MAX_LIKES_PER_SESSION}). Stopping.")
                state["profiles_seen"] -= 1
                break
            outcome = _attempt_like(n, decision.message, frames[0])
            if outcome == "like_aborted_detection":
                detection_streak += 1
                if detection_streak >= 2:
                    raise Halt("like_aborted_detection twice in a row (layout changed?)")
            else:
                detection_streak = 0
        else:
            outcome = do_skip()
            _flag(n, outcome, frames[0])
        if outcome in runlog.LIKE_OUTCOMES:
            state["likes_sent"] += 1
        elif outcome in ("skipped", "skip_unconfirmed") or outcome.startswith("like_aborted"):
            state["skips"] += 1
        t_act = time.monotonic() - t2

        timing = {
            "capture_seconds": round(t_capture, 2),
            "judge_seconds":   round(t_judge, 2),
            "act_seconds":     round(t_act, 2),
            "total_seconds":   round(t_capture + t_judge + t_act, 2),
        }
        metrics.log_profile(n, decision, timing, outcome=outcome, run_id=runlog.run_id())
        state["total_cost"] += metrics.estimated_cost(decision.usage)
        state["total_seconds"] += timing["total_seconds"]
        metrics.print_running_totals(
            n, state["likes_sent"], state["skips"],
            state["total_cost"], state["total_seconds"],
        )


def _attempt_like(n: int, message: str, frame0: bytes) -> str:
    """do_like with outcome bookkeeping. Aborted likes (nothing sent) are
    recovered by skipping; anything that leaves the send state unknown
    halts the run."""
    try:
        outcome = do_like(message)
    except LikeAborted as e:
        _flag(n, e.outcome, frame0, e)
        log.info("Like aborted before sending — skipping this profile.")
        try:
            recover_from_failed_like()
        except Exception as e2:
            log.warning(f"Recovery skip failed: {e2!r} — loop will retry next iter.")
        return e.outcome
    except ui.UnsafeTap as e:
        _flag(n, "unsafe_tap", frame0, e)
        raise Halt(f"unsafe_tap: {e}") from e
    except LikeUnconfirmed as e:
        _flag(n, "like_unconfirmed", frame0, e)
        raise Halt(f"like_unconfirmed: {e}") from e
    except Exception as e:
        # Unknown failure mid-like: we can't tell whether Send fired, so
        # treat it like an unconfirmed send and stop.
        _flag(n, "like_unconfirmed", frame0, e)
        raise Halt(f"like_unconfirmed (unexpected {e!r})") from e
    _flag(n, outcome, frame0)
    return outcome

if __name__ == "__main__":
    sys.exit(main())
