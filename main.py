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
import identity
import metrics
import opener
import openerlog
import runlog
import target
import ui
import vision
from identity import ConfirmBaseline, ProfileIdentity
from judge_common import load_backend

_backend = load_backend()
judge = _backend.judge
# Openers sent this run (repetition check); reset at loop start.
_history = opener.OpenerHistory()
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
    crop = identity.region_crop(Image.open(io.BytesIO(png)))
    return hashlib.md5(crop.tobytes()).hexdigest()


def finalize_opener(decision) -> str:
    """Stage 2: choose the comment that goes out with a like (opener.finalize)."""
    return opener.finalize(decision, getattr(_backend, "complete_json", None), _history)


def capture_profile() -> tuple[list[bytes], ProfileIdentity]:
    """Scroll through the current profile, returning its PNG frames and the
    identity recorded at frames[0] (spec §6): the "Skip <name>" label from
    a UI dump taken right after frames[0] (None if unreadable), plus a
    thumbnail and region hash of frames[0].

    A failed dump, or one with no "Skip <name>" label, is retried once after
    ~1 s, still at frames[0] (R23); if the label is still missing the
    identity has none and the loop won't attempt a like on the phone."""
    # Defensive: new profiles load at the top, so this is just guarding
    # against the app being mid-scroll from a prior partial action. A
    # handful of swipes is enough — full 18-swipe sweep isn't needed
    # because we aren't recovering from a 7-frame scroll-down.
    scroll_back_to_top(swipes=5)

    frames = []
    frames.append(adb.screenshot())
    nodes = _capture_dump()
    if identity.skip_label(nodes) is None:
        time.sleep(1.0)
        nodes = _capture_dump() or nodes
        if identity.skip_label(nodes) is None:
            log.warning("No 'Skip <name>' label at capture after one retry; "
                        "identity has no name label.")
    ident = identity.capture(frames[0], nodes, top_hash=_profile_region_hash(frames[0]))
    adb.jitter_sleep("after_screenshot")

    for _ in range(config.FRAMES_PER_PROFILE - 1):
        adb.scroll_down()
        adb.jitter_sleep("after_scroll")
        frames.append(adb.screenshot())
        adb.jitter_sleep("after_screenshot")

    return frames, ident


def _capture_dump() -> list | None:
    try:
        return ui.ui_texts()
    except Exception as e:
        log.warning(f"UI dump at capture failed ({e!r}).")
        return None


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


def scroll_to_verified_top() -> bytes | None:
    """Swipe up one at a time, screenshotting after each, until two
    consecutive frames' thumbnails near-match (identity.near_match: the view
    stopped moving, so it is the top). A near-match rather than md5 equality
    so a playing video or animated photo still settles (R22). Returns that
    PNG, or None if it never settles within FRAMES_PER_PROFILE * 2 + 4
    swipes."""
    prev_thumb = identity.thumb(adb.screenshot())
    for _ in range(config.FRAMES_PER_PROFILE * 2 + 4):
        adb.scroll_up()
        adb.jitter_sleep("after_scroll")
        cur = adb.screenshot()
        cur_thumb = identity.thumb(cur)
        if identity.near_match(prev_thumb, cur_thumb):
            return cur
        prev_thumb = cur_thumb
    return None


def _verify_identity(ident: ProfileIdentity) -> tuple[bytes, list | None]:
    """Screenshot + UI dump; Halt unless it's the judged profile at the
    top. A no_label read is retried IDENTITY_RETRIES times. Returns the verified screenshot and dump (None if the dump failed,
    only possible on the emulator); the heart is located from that same
    dump, so the label that proved identity is the one tapped against."""
    for attempt in range(1 + config.IDENTITY_RETRIES):
        if attempt:
            # no_label is usually a dump that timed out while the screen was
            # still moving (run 20261103-074411); give it time to settle.
            # Any other reason means a different screen: never retried.
            time.sleep(config.IDENTITY_RETRY_WAIT)
        png = adb.screenshot()
        try:
            nodes = ui.ui_texts()
        except ui.UiDumpFailed:
            nodes = None
        reason = identity.verify(ident, png, nodes)
        if reason != "no_label":
            break
    runlog.event("target", target="identity", result=reason or "ok",
                 detail={"attempts": attempt + 1})
    if reason is not None:
        raise Halt(f"identity_mismatch: {reason}")
    return png, nodes


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
    covering the skip X). Hide the keyboard if it's up, then skip.

    The compose card's Rose pill can sit near the skip point, so on the
    phone the skip tap only happens if the on-screen "Skip <name>" control
    covers it; otherwise raise ui.UnsafeTap (the loop halts)."""
    adb.hide_keyboard()
    if config.DEVICE != "emulator":
        x, y = config.COORDS["skip_button"]
        try:
            nodes = ui.ui_texts()
        except ui.UiDumpFailed as e:
            raise ui.UnsafeTap(f"screen unknown before recovery skip: {e}") from e
        if not any(n.label.startswith("Skip ") and n.contains(x, y) for n in nodes):
            raise ui.UnsafeTap(f"no 'Skip …' control at {x, y} for recovery skip")
    do_skip()


def do_like(message: str, ident: ProfileIdentity, anchor: dict | None = None) -> str:
    """In live mode: scroll to top, verify it's the judged profile, tap heart,
    type message (if any), tap Send Like, confirm the profile advanced.
    In dry run: advance by skipping (so we never send an actual like).

    `ident` comes unchanged from capture_profile(); if the screen isn't that
    profile, Halt("identity_mismatch: ...") before any tap.

    Targeting (spec §6): a prompt `anchor` on the phone scans down for that
    prompt's "Like prompt" heart (_locate_prompt_heart); not found, or lost
    on re-dump, falls back BEFORE any tap to photo 1 on the same profile
    (scroll back, re-verify). Everything else (no anchor, photo/bio anchors,
    the emulator) uses photo 1. After a heart tap there is no retargeting.

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
    top, nodes = _verify_identity(ident)
    located = None
    if _wants_prompt(anchor):
        located = _locate_prompt_heart(anchor, ident, top, nodes)
        if isinstance(located, str):           # pre-tap fallback reason
            runlog.event("target", target="prompt", result="fallback_photo1",
                         detail={"reason": located})
            log.info(f"Prompt heart not usable ({located}) — liking photo 1.")
            located = None
            scroll_back_to_top()
            top, nodes = _verify_identity(ident)
    if located is not None:
        heart_xy, png, label, nodes = located
        baseline = ConfirmBaseline(pre_tap_hash=_profile_region_hash(png),
                                   pre_tap_viewport="scrolled", identity=ident,
                                   matched_prompt_label=label)
        runlog.event("detect", target="heart", result=heart_xy,
                     detail={"source": "prompt_label"})
    else:
        baseline = ConfirmBaseline(pre_tap_hash=_profile_region_hash(top),
                                   pre_tap_viewport="top", identity=ident,
                                   matched_prompt_label=None)
        # Heart from the verified dump only (no second, unchecked dump); a
        # failed dump (emulator) goes straight to the pixel fallback.
        heart_xy, source = (_like_photo_xy(nodes) if nodes is not None else None), "label"
        if heart_xy is None:
            heart_xy, source = vision.find_first_heart(top), "pixels"
        runlog.event("detect", target="heart", result=heart_xy, detail={"source": source})
        if heart_xy is None:
            # Static fallback used to fire here, but it silently misses on
            # profiles where the heart's real position differs from the
            # calibrated coord (different layouts, partial scroll-back). The
            # loop would then type/tap into the void and never advance,
            # producing an infinite-loop on the same profile. Bail to skip
            # instead so the profile advances and the loop survives.
            raise LikeAborted("like_aborted_detection",
                              "couldn't find photo-1 heart (no 'Like photo' label, "
                              "no pixel match) after scroll-back")
    if nodes is not None:
        _refuse_if_on_rose(nodes, heart_xy, "heart")
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
        # Poll until clearly visible text is in the box. Not length-scaled:
        # on the S23 the box scrolls and shows ~2 lines of any long opener
        # (a 76-char opener read 2438 px), so this catches "nothing typed",
        # not a truncated opener.
        min_pixels = config.COMPOSE["text_min_pixels"]
        deadline = time.monotonic() + 15
        current = 0
        while time.monotonic() < deadline:
            time.sleep(1.0)
            current = vision.comment_field_text_pixels(adb.screenshot(), send_xy)
            if current >= min_pixels:
                break
        runlog.event("detect", target="comment_text", result=current,
                     detail={"empty": empty_pixels, "min": min_pixels,
                             "chars": len(message)})
        if current < min_pixels:
            # Don't send an empty opener. Raising lands in the loop's
            # do_like recovery (skip), so nothing goes out.
            raise LikeAborted(
                "like_aborted_text",
                f"typed text didn't land (text pixels {current}, want "
                f">= {min_pixels}) — not sending")
        # Typed text can wrap to multiple lines, expanding the comment
        # field and pushing Send Like down. Re-find against the post-type
        # screen so the tap lands on the actual button position.
        post_type_xy = vision.find_send_like(adb.screenshot())
        if post_type_xy is not None:
            send_xy = post_type_xy
    _guard_send_tap(send_xy)
    adb.tap(*send_xy, target="send_like")
    adb.jitter_sleep("after_like_sent")
    return _confirm_like_sent(baseline)



def _guard_send_tap(send_xy: tuple[int, int]) -> None:
    """R24: one fresh dump right before Send Like; refuse (ui.UnsafeTap) if
    the point lies on a Rose element. An unreadable dump leaves the screen
    state uncertain with the compose card open and nothing sent: Halt rather
    than tap. On the emulator (labels may be unavailable, R5) a failed dump
    skips the guard, as for the heart tap."""
    try:
        nodes = ui.ui_texts()
    except ui.UiDumpFailed as e:
        if config.DEVICE == "emulator":
            return
        raise Halt("send_guard_unreadable") from e
    _refuse_if_on_rose(nodes, send_xy, "send_like")


def _wants_prompt(anchor) -> bool:
    """Prompt targeting only for prompt anchors on the phone (on the
    emulator labels may be missing: photo 1 only, ruling R5)."""
    return (config.DEVICE != "emulator" and isinstance(anchor, dict)
            and anchor.get("kind") == "prompt" and bool(anchor.get("prompt_text")))


def _refuse_if_on_rose(nodes: list, xy: tuple[int, int], what: str) -> None:
    for n in nodes:
        if ui._is_rose_node(n) and n.contains(*xy):
            raise ui.UnsafeTap(f"{what} {tuple(xy)} lies inside {n.label!r}")


def _checked_dump(ident: ProfileIdentity, step: str) -> tuple[bytes, list] | None:
    """Screenshot + dump on the scrolled profile, for continuity on the
    judged profile (the thumbnail can't be compared away from the top).
    A DIFFERENT "Skip <name>" -> Halt. An absent label or unreadable dump ->
    None: the caller falls back to photo 1 (ruling R17), whose path
    re-verifies label + thumbnail at the top before any tap."""
    png = adb.screenshot()
    try:
        nodes = ui.ui_texts()
    except ui.UiDumpFailed:
        return None
    label = identity.skip_label(nodes)
    if label is None:
        return None
    if label != ident.skip_label:
        runlog.event("target", target="identity", result="name_mismatch",
                     detail={"step": step})
        raise Halt(f"identity_mismatch: name_mismatch ({step})")
    return png, nodes


def _nudge_down() -> None:
    """Half a scroll: lifts a low heart without pushing its card off the top."""
    c = config.COORDS
    (fx, fy), (_, ty) = c["scroll_from"], c["scroll_to"]
    adb.swipe(fx, fy, fx, (fy + ty) // 2, c["scroll_duration_ms"])
    adb.jitter_sleep("after_scroll")


def _locate_prompt_heart(anchor: dict, ident: ProfileIdentity, top: bytes, nodes: list):
    """Scan from the verified top view (`top`, `nodes`) down up to
    TARGET["max_scroll_steps"] scrolls for the anchor's prompt.

    Returns (heart_xy, png, matched_prompt_label, nodes) where png/nodes are
    the label-checked screenshot and dump the heart came from; or a
    fallback reason string ("prompt_not_found" / "match_lost" /
    "no_label_scrolled") when no heart was tapped and photo 1 should be
    used. Halts when a different profile's name shows up."""
    t = config.TARGET
    png = top
    for step in range(t["max_scroll_steps"] + 1):
        if step:
            adb.scroll_down()
            adb.jitter_sleep("after_scroll")
            checked = _checked_dump(ident, f"scan step {step}")
            if checked is None:
                return "no_label_scrolled"
            png, nodes = checked
        found = target.find_prompt_heart(nodes, anchor)
        if found is not None:
            break
    else:
        return "prompt_not_found"
    prompt, heart, score = found
    runlog.event("target", target="prompt", result="prompt_matched",
                 detail={"score": round(score, 3), "step": step, "label": prompt.label[:80]})
    if heart.center[1] > t["low_y_frac"] * config.SCREEN_HEIGHT:
        _nudge_down()
        checked = _checked_dump(ident, "nudge")
        if checked is None:
            return "no_label_scrolled"
        png, nodes = checked
        found = target.find_prompt_heart(nodes, anchor)
        if found is None:
            return "match_lost"
        prompt, heart, _ = found
    y = heart.center[1]
    if not (120 <= y <= config.SCREEN_HEIGHT - 220):   # under status bar / nav
        return "match_lost"
    return heart.center, png, prompt.label, nodes

def _like_photo_xy(nodes: list | None = None) -> tuple[int, int] | None:
    """Center of the topmost "Like photo" control (photo 1's heart after
    scroll-back), from `nodes` (or a fresh dump if None); None if absent or
    unreadable.

    Preferred over pixel detection: dark strokes in a photo can merge with
    the dark heart circle and defeat vision.find_first_heart.
    """
    if nodes is None:
        try:
            nodes = ui.ui_texts()
        except ui.UiDumpFailed:
            return None
    hearts = [n for n in nodes if n.label == "Like photo"]
    return min(hearts, key=lambda n: n.y0).center if hearts else None


def _read_screen_labels() -> list:
    try:
        return ui.ui_texts()
    except ui.UiDumpFailed as e:
        raise LikeUnconfirmed(f"screen unknown after send: {e}") from e


def _confirm_like_sent(baseline: ConfirmBaseline) -> str:
    """After Send Like: handle Hinge's "Send a Rose instead?" upsell sheet,
    require that the compose card is gone, then (spec §6) require that the
    profile advanced, judged on a verified top view:
      1. quick rejects on the raw screen: region hash == pre_tap_hash, or the
         matched prompt label is still on screen;
      2. scroll_to_verified_top() (never settles -> unconfirmed);
      3. the top thumbnail must NOT near-match the judged profile's.
    The "Skip <name>" label is not evidence: the next profile can share it.

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
    runlog.save_frame("post_send", after)
    send_after = vision.find_send_like(after)
    after_hash = _profile_region_hash(after)
    runlog.event("detect", target="post_send", result=send_after,
                 detail={"hash_changed": after_hash != baseline.pre_tap_hash})
    if send_after is not None:
        raise LikeUnconfirmed("Send Like still visible after send")
    # 1. Quick rejects on the raw post-send screen.
    if after_hash == baseline.pre_tap_hash:
        raise LikeUnconfirmed("profile did not advance after send")
    # Re-read right before step 2: its scroll-up gesture is a drag down,
    # which could dismiss a sheet that slid in, so check while it's visible.
    pre_swipe = _read_screen_labels()
    _raise_if_sheet(pre_swipe, "before top normalisation")
    if baseline.matched_prompt_label is not None and any(
            n.label == baseline.matched_prompt_label for n in nodes + pre_swipe):
        raise LikeUnconfirmed("matched prompt still on screen after send")
    # 2. Normalise to a top view proven by two equal consecutive hashes.
    top = scroll_to_verified_top()
    if top is None:
        raise LikeUnconfirmed("top view never stabilised after send")
    if vision.find_send_like(top) is not None:
        raise LikeUnconfirmed("Send Like visible on the top view after send")
    # 3. Positive evidence: the top is no longer the judged profile.
    try:
        top_thumb = identity.thumb(top)
    except Exception as e:
        raise LikeUnconfirmed(f"top view unreadable after send: {e!r}") from e
    still_there = identity.near_match(top_thumb, baseline.identity.top_thumb)
    runlog.event("detect", target="post_send_top",
                 result="same_profile" if still_there else "advanced")
    if still_there:
        raise LikeUnconfirmed("profile did not advance after send "
                              "(top view still matches the judged profile)")
    # A sheet can slide in after the first read; check once more before
    # counting the like. That read must also show we're back on Discover
    # (R26): a "Skip <name>" control or the "Discover" nav label.
    final = _read_screen_labels()
    _raise_if_sheet(final, "after top normalisation")
    if not any(isinstance(n.label, str)
               and (n.label.startswith("Skip ") or n.label == "Discover") for n in final):
        raise LikeUnconfirmed("post-send screen not Discover")
    return outcome


def _raise_if_sheet(nodes: list, when: str) -> None:
    if ui.find_label(nodes, "Close sheet") or ui.find_label(nodes, "Send Like anyway"):
        labels = [n.label for n in nodes][:10]
        raise LikeUnconfirmed(f"late sheet after send ({when}): {labels}")


def save_debug(frames: list[bytes], decision, profile_idx: int,
               opener_status: str | None = None) -> Path | None:
    """Frames + decision.txt under debug/<liked|skipped>/<run_id>/NN_name/
    (one folder per run, so runs don't overwrite each other). Returns the
    imgs folder, or None when SAVE_DEBUG_FRAMES is off."""
    if not config.SAVE_DEBUG_FRAMES:
        return None
    bucket = "liked" if decision.decision == "like" else "skipped"
    bucket_dir = config.DEBUG_DIR / bucket
    if runlog.run_id():
        bucket_dir = bucket_dir / runlog.run_id()
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
        f"opener_status: {opener_status}\n"
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}\n"
    )
    return imgs


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
    if config.DEVICE != "emulator" and identity.skip_label(nodes) is None:
        # Without it every like becomes a no_identity skip (2026-11-03:
        # Hinge's accessibility tree came back with no labels at all).
        reasons.append(f"no 'Skip <name>' label on screen ({len(nodes)} labels "
                       "read) - Hinge isn't exposing its accessibility labels")
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

    global _history
    _history = opener.OpenerHistory()

    last_frame0_hash: str | None = None
    duplicate_streak = 0
    detection_streak = 0

    while state["profiles_seen"] < config.MAX_PROFILES_PER_SESSION:
        state["profiles_seen"] += 1
        n = state["profiles_seen"]
        runlog.set_profile(n)
        log.info(f"\n--- Profile {n} ---")

        t0 = time.monotonic()
        frames, ident = capture_profile()
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

        # Stage 2 before logging/save_debug, so both show the comment that
        # will actually be sent.
        try:
            opener_status = finalize_opener(decision)
        except Exception as e:
            # finalize shouldn't raise; if it does, like without a comment.
            log.warning(f"Opener finalize failed: {e!r} — no comment.")
            decision.message = ""
            decision.anchor = None
            opener_status = "finalize_error"

        log.info(f"Name:     {decision.name}")
        log.info(f"Decision: {decision.decision} ({decision.confidence}) "
                 f"[{decision.skip_reason if decision.decision == 'skip' else decision.message_archetype}]")
        log.info(f"Reason:   {decision.reasoning}")
        if decision.message:
            log.info(f"Message:  {decision.message}")
        if decision.decision == "like":
            log.info(f"Opener:   {opener_status}")
        frames_dir = save_debug(frames, decision, n, opener_status)

        t2 = time.monotonic()
        if decision.decision == "like":
            if state["likes_sent"] >= config.MAX_LIKES_PER_SESSION:
                log.info(f"Hit max likes cap ({config.MAX_LIKES_PER_SESSION}). Stopping.")
                state["profiles_seen"] -= 1
                break
            if config.DEVICE != "emulator" and getattr(ident, "skip_label", None) is None:
                # R23: no "Skip <name>" at capture even after the retry, so
                # identity can't be verified. Don't attempt the like; skip.
                log.warning("No identity label at capture — not attempting the like; skipping.")
                _flag(n, "no_identity", frames[0])
                outcome = do_skip()
                _flag(n, outcome, frames[0])
            else:
                try:
                    outcome = _attempt_like(n, decision, frames[0], ident)
                except Halt as h:
                    # Halted before or during the like: still keep the opener.
                    _record_opener(n, decision, opener_status,
                                   h.reason.split(":", 1)[0], frames_dir)
                    raise
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
        _record_opener(n, decision, opener_status, outcome, frames_dir)
        state["total_cost"] += metrics.estimated_cost(decision.usage)
        state["total_seconds"] += timing["total_seconds"]
        metrics.print_running_totals(
            n, state["likes_sent"], state["skips"],
            state["total_cost"], state["total_seconds"],
        )


def _record_opener(n: int, decision, opener_status: str, outcome: str,
                   frames_dir) -> None:
    """openerlog record + live HTML. A review log must never stop the loop."""
    try:
        openerlog.record(runlog.run_id(), n, decision, opener_status,
                         outcome, frames_dir)
        if decision.decision == "like":
            openerlog.write_html()
    except Exception as e:
        log.warning(f"openerlog record/html failed: {e!r}")


def _attempt_like(n: int, decision, frame0: bytes, ident: ProfileIdentity) -> str:
    """do_like with outcome bookkeeping. Aborted likes (nothing sent) are
    recovered by skipping; anything that leaves the send state unknown
    halts the run. A Halt from do_like (identity mismatch: the screen isn't
    the judged profile) propagates untouched — no recovery skip, since
    skipping would act on an unjudged profile."""
    try:
        outcome = do_like(decision.message, ident,
                          anchor=getattr(decision, "anchor", None))
    except Halt as e:
        try:
            _flag(n, e.reason.split(":", 1)[0], frame0, e)
        except Exception:
            pass
        raise
    except LikeAborted as e:
        _flag(n, e.outcome, frame0, e)
        log.info("Like aborted before sending — skipping this profile.")
        try:
            recover_from_failed_like()
        except ui.UnsafeTap as e2:
            _flag(n, "unsafe_tap", frame0, e2)
            raise Halt(f"unsafe_tap: {e2}") from e2
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
