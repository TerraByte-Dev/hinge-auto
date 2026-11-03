"""main.do_like: typed-text guard, post-send confirmation, Rose sheet."""

from functools import lru_cache

import pytest

import adb
import config
import identity
import main
import ui
import vision
from identity import ConfirmBaseline
from tests.test_identity import nodes_for, render
from tests.test_ui import ROSE_SHEET_XML

SEND = (708, 1168)
REAL_LIKE_PHOTO_XY = main._like_photo_xy
ROSE_SHEET = ui.parse_ui_xml(ROSE_SHEET_XML)
DISCOVER = [ui.UiNode("Signals", 148, 110, 338, 196),
            ui.UiNode("Discover", 0, 2200, 200, 2340)]
OTHER_SHEET = [ui.UiNode("Close sheet", 0, 0, 1080, 2340),
               ui.UiNode("Something new", 100, 1400, 980, 1500)]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakePhone:
    """Scroll-position model. Screens are rendered PNGs keyed by
    (profile, position); the top is position 0 and scroll_up there is a
    no-op, so the region hash stabilises. Tapping Send Like sends; then the
    profile advances (to the next one, at the top) unless `advance` is False,
    in which case it stays at `after_pos` (default: where it was)."""

    def __init__(self, profiles=((1, "Sam"), (2, "Alex"))):
        self.profiles = list(profiles)   # (render seed, name)
        self.idx = 0
        self.pos = 0
        self.taps: list[tuple[int, int, str | None]] = []
        self.typed_text = []
        self.sent = False
        self.ui_sequence: list = [DISCOVER]   # dumps after Send Like
        self.pre_send_nodes = None   # override dumps before send (list or Exception)
        self.pre_send_dumps = None   # or a sequence of them, one per dump
        self.pre_send_dump_count = 0
        self.send_pill_after_scroll = False   # Send pill reappears once scrolled
        self.advance = True          # does the profile change after send?
        self.after_pos = None        # position after a non-advancing send
        self.unstable = False        # post-send screens never repeat
        self.send_after = None       # Send pill position on the after-screen
        self.typed = False
        self.text_lands = True
        self.typed_pixels = 9999     # dark pixels in the comment box once typed
        self.empty_pixels = 40       # ... before typing
        self.like_photo = None       # heart point from the "Like photo" label
        self.scroll_ups = 0
        self.scroll_ups_at_send = None
        self.shots = 0
        self.scroll_downs = 0
        self.nudges = 0              # short swipes (prompt-heart nudge)
        self.extra_nodes = {}        # (idx, pos, nudges) -> nodes added to the dump
        self.name_by_pos = {}        # pos -> name shown in "Skip <name>" there

    @property
    def seed(self):
        return self.profiles[self.idx][0]

    @property
    def name(self):
        return self.profiles[self.idx][1]

    def judged(self, idx=0, nodes="auto"):
        """Identity as capture_profile would record it for profile `idx`."""
        seed, name = self.profiles[idx]
        return identity.capture(screen(seed, 0), nodes_for(name) if nodes == "auto" else nodes)

    def text_pixels(self, png, xy):
        if self.typed and self.text_lands:
            return self.typed_pixels
        return self.empty_pixels

    def screenshot(self):
        self.shots += 1
        if self.sent and self.unstable:
            return screen(500 + self.shots, 0)
        return screen(self.seed, self.pos)

    def scroll_up(self):
        self.scroll_ups += 1
        self.pos = max(0, self.pos - 1)

    def scroll_down(self):
        self.scroll_downs += 1
        self.pos += 1

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        assert y2 < y1, "nudge must scroll content up (drag upward)"
        self.nudges += 1

    def tap(self, x, y, target=None):
        self.taps.append((x, y, target))
        if (x, y) == SEND and not self.sent:
            self.sent = True
            self.scroll_ups_at_send = self.scroll_ups
            if self.advance:
                self.idx, self.pos = self.idx + 1, 0
            elif self.after_pos is not None:
                self.pos = self.after_pos

    def input_text(self, text):
        self.typed = True
        self.typed_text.append(text)

    def ui_texts(self):
        if not self.sent:
            self.pre_send_dump_count += 1
            item = self.pre_send_dumps.pop(0) if self.pre_send_dumps else self.pre_send_nodes
            if item is None:
                return (nodes_for(self.name_by_pos.get(self.pos, self.name)) + DISCOVER
                        + self.extra_nodes.get((self.idx, self.pos, self.nudges), []))
        else:
            item = self.ui_sequence.pop(0) if len(self.ui_sequence) > 1 else self.ui_sequence[0]
        if isinstance(item, Exception):
            raise item
        return item

    def find_send_like(self, png):
        if not self.sent:
            return SEND
        if self.send_pill_after_scroll and self.scroll_ups > self.scroll_ups_at_send:
            return SEND
        return self.send_after


@lru_cache(maxsize=None)
def screen(seed, position):
    return render(seed, position)


@pytest.fixture
def phone(monkeypatch):
    p = FakePhone()
    clock = FakeClock()
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    monkeypatch.setattr(main.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(main.time, "sleep", clock.sleep)
    monkeypatch.setattr(adb, "jitter_sleep", lambda key: None)
    monkeypatch.setattr(adb, "screenshot", p.screenshot)
    monkeypatch.setattr(adb, "scroll_up", p.scroll_up)
    monkeypatch.setattr(adb, "scroll_down", p.scroll_down)
    monkeypatch.setattr(adb, "swipe", p.swipe)
    monkeypatch.setattr(adb, "tap", p.tap)
    monkeypatch.setattr(adb, "input_text", p.input_text)
    monkeypatch.setattr(ui, "ui_texts", p.ui_texts)
    monkeypatch.setattr(vision, "find_first_heart", lambda png: (917, 1388))
    monkeypatch.setattr(vision, "find_send_like", p.find_send_like)
    monkeypatch.setattr(vision, "comment_field_text_pixels", p.text_pixels)
    monkeypatch.setattr(main, "_like_photo_xy", lambda nodes=None: p.like_photo, raising=False)
    monkeypatch.setattr(main, "do_skip", lambda: pytest.fail("do_skip called"))
    p.ident = p.judged()
    return p


def _taps_after_send(p):
    i = [t[:2] for t in p.taps].index(SEND)
    return p.taps[i + 1:]


def test_do_like_aborts_instead_of_sending_when_text_missing(phone):
    phone.text_lands = False

    with pytest.raises(main.LikeAborted) as e:
        main.do_like("hello there", phone.ident)

    assert e.value.outcome == "like_aborted_text"
    assert not phone.sent


def test_missing_heart_is_detection_abort(phone, monkeypatch):
    monkeypatch.setattr(vision, "find_first_heart", lambda png: None)

    with pytest.raises(main.LikeAborted) as e:
        main.do_like("hi", phone.ident)

    assert e.value.outcome == "like_aborted_detection"


def test_confirmed_when_no_sheet_and_profile_advanced(phone):
    assert main.do_like("hi", phone.ident) == "liked_confirmed"


def test_rose_sheet_taps_send_like_anyway(phone):
    phone.ui_sequence = [ROSE_SHEET, DISCOVER]

    assert main.do_like("hi", phone.ident) == "like_sheet_handled"
    assert _taps_after_send(phone) == [(539, 2040, "send_like_anyway")]


def test_rose_sheet_persisting_raises_unconfirmed(phone):
    phone.ui_sequence = [ROSE_SHEET, ROSE_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="rose sheet persisted"):
        main.do_like("hi", phone.ident)


def test_unknown_sheet_raises_unconfirmed_without_tapping(phone):
    phone.ui_sequence = [OTHER_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="unknown sheet"):
        main.do_like("hi", phone.ident)
    assert _taps_after_send(phone) == []


def test_sheet_appearing_after_rose_sheet_is_unconfirmed(phone):
    phone.ui_sequence = [ROSE_SHEET, OTHER_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="unknown sheet"):
        main.do_like("hi", phone.ident)


def test_ui_dump_failure_after_send_is_unconfirmed(phone):
    phone.ui_sequence = [ui.UiDumpFailed("busy")]

    with pytest.raises(main.LikeUnconfirmed, match="screen unknown"):
        main.do_like("hi", phone.ident)


def test_profile_not_advanced_raises_unconfirmed(phone):
    phone.advance = False

    with pytest.raises(main.LikeUnconfirmed, match="did not advance"):
        main.do_like("hi", phone.ident)


def test_send_pill_still_visible_raises_unconfirmed(phone):
    phone.send_after = SEND

    with pytest.raises(main.LikeUnconfirmed, match="Send Like still visible"):
        main.do_like("hi", phone.ident)


def test_failed_like_recovery_hides_keyboard_before_skipping(monkeypatch):
    events = []
    monkeypatch.setattr(adb, "jitter_sleep", lambda key: None)
    monkeypatch.setattr(adb, "hide_keyboard", lambda: events.append("hide") or True)
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None: events.append(("tap", x, y)))
    monkeypatch.setattr(adb, "screenshot", lambda: b"x")
    monkeypatch.setattr(main, "_profile_region_hash", lambda png: str(len(events)))
    x, y = config.COORDS["skip_button"]
    monkeypatch.setattr(ui, "ui_texts", lambda: [ui.UiNode("Skip Su", x - 90, y - 90, x + 90, y + 90)])

    main.recover_from_failed_like()

    assert events[0] == "hide" and events[1] == ("tap", *config.COORDS["skip_button"])


# ---------- final-review fixes ----------

def test_sheet_appearing_after_confirmation_read_is_unconfirmed(phone):
    phone.ui_sequence = [DISCOVER, OTHER_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="late sheet"):
        main.do_like("hi", phone.ident)


def test_post_send_frame_is_saved(phone, monkeypatch):
    import runlog
    saved = []
    monkeypatch.setattr(runlog, "save_frame", lambda name, png: saved.append((name, png)))

    main.do_like("hi", phone.ident)

    assert ("post_send", screen(2, 0)) in saved


def test_recovery_refuses_skip_tap_without_skip_label(monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    monkeypatch.setattr(adb, "hide_keyboard", lambda: True)
    monkeypatch.setattr(adb, "tap", lambda *a, **k: pytest.fail("tapped"))
    monkeypatch.setattr(ui, "ui_texts", lambda: ROSE_SHEET)

    with pytest.raises(ui.UnsafeTap):
        main.recover_from_failed_like()


def test_recovery_skips_when_skip_label_is_at_the_skip_point(monkeypatch):
    taps = []
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    monkeypatch.setattr(adb, "hide_keyboard", lambda: True)
    monkeypatch.setattr(adb, "jitter_sleep", lambda k: None)
    monkeypatch.setattr(adb, "screenshot", lambda: b"x")
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None: taps.append((x, y)))
    monkeypatch.setattr(main, "_profile_region_hash", lambda png: str(len(taps)))
    monkeypatch.setattr(ui, "ui_texts", lambda: [ui.UiNode("Skip Su", 60, 1770, 246, 1956)])

    main.recover_from_failed_like()

    assert taps == [tuple(config.COORDS["skip_button"])]


# ---------- live run 20261101-151639 fixes ----------

LIKE_PHOTO = ui.UiNode("Like photo", 846, 1278, 990, 1422)


def test_like_photo_xy_uses_topmost_label(monkeypatch):
    lower = ui.UiNode("Like photo", 846, 2015, 990, 2159)
    monkeypatch.setattr(ui, "ui_texts", lambda: [lower, LIKE_PHOTO])

    assert main._like_photo_xy() == (918, 1350)


def test_like_photo_xy_none_when_dump_fails(monkeypatch):
    def boom():
        raise ui.UiDumpFailed("busy")

    monkeypatch.setattr(ui, "ui_texts", boom)

    assert main._like_photo_xy() is None


def test_heart_from_label_when_pixels_miss_it(phone, monkeypatch):
    # Run 20261101-151639 profile 3: dark photo strokes merged into the
    # heart circle, so vision rejected it; the label still had it.
    phone.like_photo = (918, 1350)
    monkeypatch.setattr(vision, "find_first_heart", lambda png: None)

    assert main.do_like("hi", phone.ident) == "liked_confirmed"
    assert phone.taps[0][:2] == (918, 1350)


def test_long_opener_in_scrolling_box_is_accepted(phone):
    # Run 20261101-151639 profile 4: the S23 comment box scrolls, so a
    # 76-char opener shows ~2438 dark pixels however long it is. Readings
    # replayed from the run: 2581 before typing, 2438 after.
    phone.empty_pixels = 2581
    phone.typed_pixels = 2438

    assert main.do_like("the shop more clothes goal is so valid -- what style "
                        "are you building toward?", phone.ident) == "liked_confirmed"


# ---------- identity (spec §6) ----------

def _no_action(p):
    assert p.taps == [] and p.typed_text == [] and not p.sent


def test_identity_changed_profile_halts(phone):
    phone.idx = 1            # Alex is on screen; Sam was judged

    with pytest.raises(main.Halt, match="identity_mismatch: name_mismatch"):
        main.do_like("hi", phone.ident)
    _no_action(phone)


def test_same_name_different_profile_halts(phone):
    phone.profiles = [(1, "Sam"), (2, "Sam")]
    phone.ident = phone.judged(0)
    phone.idx = 1

    with pytest.raises(main.Halt, match="identity_mismatch: thumb_mismatch"):
        main.do_like("hi", phone.ident)
    _no_action(phone)


def test_unreadable_label_at_capture_halts(phone):
    phone.ident = phone.judged(0, nodes=None)

    with pytest.raises(main.Halt, match="identity_mismatch: no_label"):
        main.do_like("hi", phone.ident)
    _no_action(phone)


def test_unreadable_label_at_verification_halts(phone):
    phone.pre_send_nodes = ui.UiDumpFailed("busy")

    with pytest.raises(main.Halt, match="identity_mismatch: no_label"):
        main.do_like("hi", phone.ident)
    _no_action(phone)


def test_transient_dump_failure_at_verification_is_retried(phone):
    """2026-11-03 run 074411: one dump timed out (screen still moving) right
    before the like and halted the run; 15s later it read fine."""
    phone.pre_send_dumps = [ui.UiDumpFailed("could not get idle state")]

    assert main.do_like("hi", phone.ident) == "liked_confirmed"


def test_persistent_dump_failure_at_verification_halts_after_retries(phone):
    phone.pre_send_nodes = ui.UiDumpFailed("busy")

    with pytest.raises(main.Halt, match="identity_mismatch: no_label"):
        main.do_like("hi", phone.ident)
    assert phone.pre_send_dump_count == 1 + config.IDENTITY_RETRIES
    _no_action(phone)


def test_name_mismatch_is_not_retried(phone):
    phone.ident = phone.judged(0)
    phone.idx = 1            # Alex is on screen; Sam was judged

    with pytest.raises(main.Halt, match="name_mismatch"):
        main.do_like("hi", phone.ident)
    assert phone.pre_send_dump_count == 1
    _no_action(phone)


def test_emulator_like_without_labels_uses_thumbnail(phone, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    phone.pre_send_nodes = ui.UiDumpFailed("no uiautomator")
    phone.ident = phone.judged(0, nodes=None)

    assert main.do_like("hi", phone.ident) == "liked_confirmed"


def test_emulator_wrong_profile_still_halts(phone, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    phone.pre_send_nodes = ui.UiDumpFailed("no uiautomator")
    phone.ident = phone.judged(0, nodes=None)
    phone.idx = 1

    with pytest.raises(main.Halt, match="thumb_mismatch"):
        main.do_like("hi", phone.ident)
    _no_action(phone)


def test_verification_runs_after_scroll_back(phone):
    phone.pos = 3            # left scrolled down by capture

    assert main.do_like("hi", phone.ident) == "liked_confirmed"


def test_dry_run_skips_before_verification(phone, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(main, "do_skip", lambda: "skipped")
    phone.idx = 1            # wrong profile: irrelevant in dry run

    assert main.do_like("hi", phone.ident) == "skipped"


def test_capture_profile_records_identity_at_frame0(phone, monkeypatch):
    monkeypatch.setattr(config, "FRAMES_PER_PROFILE", 3)
    phone.pos = 2

    frames, ident = main.capture_profile()

    assert len(frames) == 3 and frames[0] == screen(1, 0)
    assert ident.skip_label == "Skip Sam"
    assert ident.top_thumb == identity.thumb(frames[0])
    assert ident.top_hash == main._profile_region_hash(frames[0])


def test_capture_profile_dump_failure_gives_no_label(phone, monkeypatch):
    monkeypatch.setattr(config, "FRAMES_PER_PROFILE", 2)
    phone.pre_send_nodes = ui.UiDumpFailed("busy")

    frames, ident = main.capture_profile()

    assert len(frames) == 2 and ident.skip_label is None
    assert phone.pre_send_dump_count == 2      # one retry (R23)
    with pytest.raises(main.Halt, match="no_label"):
        main.do_like("hi", ident)


def test_attempt_like_reraises_halt_without_recovery(phone, monkeypatch):
    monkeypatch.setattr(main, "recover_from_failed_like", lambda: pytest.fail("recovered"))
    phone.idx = 1
    decision = type("D", (), {"message": "hi", "anchor": None})()

    with pytest.raises(main.Halt) as e:
        main._attempt_like(1, decision, screen(1, 0), phone.ident)
    assert e.value.reason == "identity_mismatch: name_mismatch"
    _no_action(phone)


def test_attempt_like_passes_anchor(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "do_like",
                        lambda m, i, anchor=None: seen.update(m=m, i=i, a=anchor) or "liked_confirmed")
    decision = type("D", (), {"message": "hi", "anchor": {"id": "f1"}})()

    assert main._attempt_like(1, decision, b"f", "IDENT") == "liked_confirmed"
    assert seen == {"m": "hi", "i": "IDENT", "a": {"id": "f1"}}


# ---------- normalised send confirmation (spec §6 steps 1-3) ----------

def _baseline(p, pos, viewport="scrolled", label=None):
    return ConfirmBaseline(pre_tap_hash=main._profile_region_hash(screen(p.seed, pos)),
                           pre_tap_viewport=viewport, identity=p.ident,
                           matched_prompt_label=label)


def test_confirm_same_position_unconfirmed(phone):
    phone.sent, phone.pos = True, 1          # compose gone, still at pre-tap pos

    with pytest.raises(main.LikeUnconfirmed, match="did not advance after send$"):
        main._confirm_like_sent(_baseline(phone, 1))
    assert phone.scroll_ups == 0             # rejected at step 1


def test_confirm_shifted_third_position_unconfirmed(phone):
    phone.sent, phone.pos = True, 2          # != pre-tap 1, != top
    label = "Prompt: My simple pleasures"

    with pytest.raises(main.LikeUnconfirmed, match="still matches the judged profile"):
        main._confirm_like_sent(_baseline(phone, 1, label=label))
    assert phone.pos == 0 and phone.scroll_ups == 3   # 2 -> 1 -> 0 -> 0 (stable)


def test_confirm_matched_prompt_label_still_present_unconfirmed(phone):
    label = "Prompt: My simple pleasures"
    phone.sent, phone.pos = True, 2
    phone.ui_sequence = [DISCOVER + [ui.UiNode(label, 40, 900, 1040, 1100)]]

    with pytest.raises(main.LikeUnconfirmed, match="matched prompt still on screen"):
        main._confirm_like_sent(_baseline(phone, 1, label=label))
    assert phone.scroll_ups == 0


def test_confirm_top_never_stable_unconfirmed(phone):
    phone.unstable = True

    with pytest.raises(main.LikeUnconfirmed, match="never stabilised"):
        main.do_like("hi", phone.ident)
    assert phone.scroll_ups - phone.scroll_ups_at_send == config.FRAMES_PER_PROFILE * 2 + 4


def test_confirm_genuine_advance_same_name_ok(phone):
    phone.profiles = [(1, "Sam"), (2, "Sam")]
    phone.ident = phone.judged(0)

    assert main.do_like("hi", phone.ident) == "liked_confirmed"
    assert phone.scroll_ups - phone.scroll_ups_at_send == 1   # one stabilising swipe
    assert phone.idx == 1 and phone.pos == 0


def test_confirm_photo1_not_advanced_unconfirmed_without_scrolling(phone):
    phone.advance = False

    with pytest.raises(main.LikeUnconfirmed, match="did not advance after send$"):
        main.do_like("hi", phone.ident)
    assert phone.scroll_ups == phone.scroll_ups_at_send


def test_late_sheet_checked_after_top_normalisation(phone):
    phone.ui_sequence = [DISCOVER, DISCOVER, OTHER_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="late sheet after send \\(after top"):
        main.do_like("hi", phone.ident)
    assert phone.scroll_ups - phone.scroll_ups_at_send == 1   # step 2 ran first


def test_sheet_before_swipe_unconfirmed_even_if_swipe_would_dismiss_it(phone):
    # Fix round 1: sheet visible in the pre-swipe read, gone afterwards.
    phone.ui_sequence = [DISCOVER, OTHER_SHEET, DISCOVER]

    with pytest.raises(main.LikeUnconfirmed, match="late sheet after send \\(before top"):
        main.do_like("hi", phone.ident)
    assert phone.scroll_ups == phone.scroll_ups_at_send       # no swipe made


def test_send_pill_on_verified_top_unconfirmed(phone):
    phone.send_pill_after_scroll = True

    with pytest.raises(main.LikeUnconfirmed, match="Send Like visible on the top view"):
        main.do_like("hi", phone.ident)


def test_scroll_to_verified_top_returns_stable_frame(phone):
    phone.pos = 2

    assert main.scroll_to_verified_top() == screen(1, 0)
    assert phone.scroll_ups == 3


# ---------- fix round 1: heart comes from the verified dump ----------

def test_heart_taken_from_verified_dump_not_a_second_dump(phone, monkeypatch):
    monkeypatch.setattr(main, "_like_photo_xy", REAL_LIKE_PHOTO_XY)
    monkeypatch.setattr(vision, "find_first_heart", lambda png: pytest.fail("pixel path"))
    verified = nodes_for("Sam") + [LIKE_PHOTO]
    other = nodes_for("Alex") + [ui.UiNode("Like photo", 100, 300, 200, 400)]
    phone.pre_send_dumps = [verified, other, other]

    assert main.do_like("hi", phone.ident) == "liked_confirmed"
    assert phone.taps[0][:2] == LIKE_PHOTO.center
    assert phone.pre_send_dump_count == 2      # verify + Send-tap Rose guard


def test_emulator_failed_dump_goes_to_pixel_heart(phone, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    monkeypatch.setattr(main, "_like_photo_xy", REAL_LIKE_PHOTO_XY)
    phone.pre_send_nodes = ui.UiDumpFailed("no uiautomator")
    phone.ident = phone.judged(0, nodes=None)

    assert main.do_like("hi", phone.ident) == "liked_confirmed"
    assert phone.taps[0][:2] == (917, 1388)
    assert phone.pre_send_dump_count == 2      # verify + Send-tap Rose guard


# ---------- prompt targeting (spec §6, Task 6) ----------

import runlog  # noqa: E402

PROMPT_LABEL = "Prompt: This year, I really want to. Answer: Explore......."
PROMPT_ANCHOR = {"id": "f2", "kind": "prompt", "index": 1,
                 "prompt_text": "This year, I really want to",
                 "answer_text": "Explore more of the coast", "detail": ""}
OTHER_PROMPT = "Prompt: My simple pleasures. Answer: Coffee"


def _card(label, y0, heart_y0):
    """A prompt card node and its "Like prompt" heart (144px square)."""
    return [ui.UiNode(label, 40, y0, 1040, heart_y0 + 160),
            ui.UiNode("Like prompt", 846, heart_y0, 990, heart_y0 + 144)]


@pytest.fixture
def events(monkeypatch):
    got = []
    monkeypatch.setattr(runlog, "event", lambda kind, **kw: got.append((kind, kw)))
    return got


def _targets(events):
    return [(kw.get("target"), kw.get("result"), kw.get("detail"))
            for kind, kw in events if kind == "target"]


def _hearts(p):
    return [t for t in p.taps if t[2] == "heart"]


@pytest.fixture
def baselines(monkeypatch):
    got = []
    real = main._confirm_like_sent
    monkeypatch.setattr(main, "_confirm_like_sent", lambda b: got.append(b) or real(b))
    return got


def test_prompt_matched_on_step_2_taps_that_heart(phone, events, baselines):
    cards = _card(OTHER_PROMPT, 200, 500) + _card(PROMPT_LABEL, 700, 1000)
    phone.extra_nodes[(0, 2, 0)] = cards
    heart = cards[3]

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert _hearts(phone) == [(*heart.center, "heart")]
    assert phone.scroll_downs == 2 and phone.nudges == 0
    (b,) = baselines
    assert b.pre_tap_viewport == "scrolled"
    assert b.matched_prompt_label == PROMPT_LABEL
    assert b.pre_tap_hash == main._profile_region_hash(screen(1, 2))
    (res,) = [r for r in _targets(events) if r[0] == "prompt"]
    assert res[1] == "prompt_matched" and res[2]["score"] >= 0.8


def test_low_heart_gets_one_nudge_and_redump(phone):
    phone.extra_nodes[(0, 1, 0)] = _card(PROMPT_LABEL, 1500, 1900)   # y > 0.55 * 2340
    nudged = _card(PROMPT_LABEL, 1000, 1100)
    phone.extra_nodes[(0, 1, 1)] = nudged

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert phone.nudges == 1
    assert _hearts(phone) == [(*nudged[1].center, "heart")]


def test_match_lost_after_nudge_falls_back_to_photo1(phone, events, monkeypatch):
    monkeypatch.setattr(main, "recover_from_failed_like", lambda: pytest.fail("recovered"))
    phone.extra_nodes[(0, 1, 0)] = _card(PROMPT_LABEL, 1500, 1900)

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert phone.nudges == 1
    assert _hearts(phone) == [(917, 1388, "heart")]
    assert ("prompt", "fallback_photo1", {"reason": "match_lost"}) in _targets(events)


def test_prompt_not_found_falls_back_to_photo1_same_profile(phone, events, monkeypatch, baselines):
    monkeypatch.setattr(main, "recover_from_failed_like", lambda: pytest.fail("recovered"))
    taps_at = []
    real_tap = phone.tap
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None:
                        taps_at.append((phone.idx, phone.pos)) or real_tap(x, y, target))

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert phone.scroll_downs == config.TARGET["max_scroll_steps"]
    assert _hearts(phone) == [(917, 1388, "heart")]
    assert taps_at[0] == (0, 0)                 # judged profile, back at the top
    assert phone.typed_text == ["hi"]
    assert baselines[0].pre_tap_viewport == "top"
    assert baselines[0].matched_prompt_label is None
    assert ("prompt", "fallback_photo1", {"reason": "prompt_not_found"}) in _targets(events)
    # identity re-verified at the top after the fallback scroll-back
    assert [r[1] for r in _targets(events) if r[0] == "identity"] == ["ok", "ok"]


def test_fallback_reverifies_identity_at_top(phone, monkeypatch):
    # Scan steps show Sam, but after scrolling back the top is someone else.
    phone.name_by_pos = {0: "Sam"}
    real_up = phone.scroll_up

    def up():
        real_up()
        if phone.scroll_downs == config.TARGET["max_scroll_steps"]:
            phone.idx = 1      # swapped while scrolling back

    monkeypatch.setattr(adb, "scroll_up", up)

    with pytest.raises(main.Halt, match="identity_mismatch"):
        main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR)
    assert _hearts(phone) == []


def test_send_like_missing_after_prompt_tap_aborts(phone, monkeypatch):
    phone.extra_nodes[(0, 1, 0)] = _card(PROMPT_LABEL, 700, 1000)
    monkeypatch.setattr(vision, "find_send_like", lambda png: None)

    with pytest.raises(main.LikeAborted) as e:
        main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR)

    assert e.value.outcome == "like_aborted_detection"
    assert len(_hearts(phone)) == 1 and phone.typed_text == []
    assert phone.scroll_ups == config.FRAMES_PER_PROFILE * 2 + 4   # no scroll-back retarget


def test_name_change_mid_scan_halts(phone):
    phone.name_by_pos = {2: "Alex"}
    phone.extra_nodes[(0, 3, 0)] = _card(PROMPT_LABEL, 700, 1000)

    with pytest.raises(main.Halt, match="identity_mismatch"):
        main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR)
    _no_action(phone)


def test_name_change_on_nudge_redump_halts(phone, monkeypatch):
    phone.extra_nodes[(0, 1, 0)] = _card(PROMPT_LABEL, 1500, 1900)
    phone.extra_nodes[(0, 1, 1)] = _card(PROMPT_LABEL, 1000, 1100)
    real_swipe = phone.swipe

    def swipe(*a):
        real_swipe(*a)
        phone.name_by_pos = {1: "Alex"}

    monkeypatch.setattr(adb, "swipe", swipe)

    with pytest.raises(main.Halt, match="identity_mismatch"):
        main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR)
    _no_action(phone)


def test_unreadable_dump_mid_scan_falls_back_to_photo1(phone, events, monkeypatch):
    # Ruling R17: no heart tapped yet; photo 1 re-verifies at the top.
    monkeypatch.setattr(main, "recover_from_failed_like", lambda: pytest.fail("recovered"))
    phone.pre_send_dumps = [nodes_for("Sam") + DISCOVER, ui.UiDumpFailed("busy")]

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert _hearts(phone) == [(917, 1388, "heart")]
    assert ("prompt", "fallback_photo1", {"reason": "no_label_scrolled"}) in _targets(events)


def test_label_absent_at_step_2_falls_back_after_reverification(phone, events, monkeypatch):
    monkeypatch.setattr(main, "recover_from_failed_like", lambda: pytest.fail("recovered"))
    phone.name_by_pos = {2: None}
    phone.extra_nodes[(0, 3, 0)] = _card(PROMPT_LABEL, 700, 1000)   # never reached
    taps_at = []
    real_tap = phone.tap
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None:
                        taps_at.append((phone.idx, phone.pos)) or real_tap(x, y, target))

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert phone.scroll_downs == 2
    assert _hearts(phone) == [(917, 1388, "heart")] and taps_at[0] == (0, 0)
    assert ("prompt", "fallback_photo1", {"reason": "no_label_scrolled"}) in _targets(events)
    assert [r[1] for r in _targets(events) if r[0] == "identity"] == ["ok", "ok"]


def test_label_absent_on_nudge_redump_falls_back(phone, events):
    phone.extra_nodes[(0, 1, 0)] = _card(PROMPT_LABEL, 1500, 1900)
    phone.extra_nodes[(0, 1, 1)] = _card(PROMPT_LABEL, 1000, 1100)
    phone.pre_send_dumps = [nodes_for("Sam") + DISCOVER,
                            nodes_for("Sam") + DISCOVER + _card(PROMPT_LABEL, 1500, 1900),
                            ui.UiDumpFailed("busy")]

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert phone.nudges == 1 and _hearts(phone) == [(917, 1388, "heart")]
    assert ("prompt", "fallback_photo1", {"reason": "no_label_scrolled"}) in _targets(events)


def test_different_label_at_step_2_halts_without_tap(phone):
    phone.name_by_pos = {2: "Alex"}

    with pytest.raises(main.Halt, match="identity_mismatch: name_mismatch"):
        main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR)
    _no_action(phone)


def test_prompt_heart_on_rose_refused(phone):
    cards = _card(PROMPT_LABEL, 700, 1000)
    rose = ui.UiNode("Send a Rose", 800, 950, 1040, 1200)
    phone.extra_nodes[(0, 1, 0)] = cards + [rose]

    with pytest.raises(ui.UnsafeTap):
        main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR)
    _no_action(phone)


def test_emulator_skips_prompt_path(phone, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    phone.extra_nodes[(0, 0, 0)] = _card(PROMPT_LABEL, 700, 1000)

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"

    assert phone.scroll_downs == 0
    assert _hearts(phone) == [(917, 1388, "heart")]


def test_photo_anchor_uses_photo1(phone):
    anchor = {"id": "f1", "kind": "photo", "index": 3, "prompt_text": "",
              "answer_text": "", "detail": "dog"}

    assert main.do_like("hi", phone.ident, anchor=anchor) == "liked_confirmed"
    assert phone.scroll_downs == 0 and _hearts(phone) == [(917, 1388, "heart")]


def test_prompt_on_top_view_needs_no_scroll(phone):
    cards = _card(PROMPT_LABEL, 700, 1000)
    phone.extra_nodes[(0, 0, 0)] = cards

    assert main.do_like("hi", phone.ident, anchor=PROMPT_ANCHOR) == "liked_confirmed"
    assert phone.scroll_downs == 0 and _hearts(phone) == [(*cards[1].center, "heart")]


# ---------- final whole-branch review fixes (R22-R26) ----------

import numpy as np  # noqa: E402
import io  # noqa: E402
from PIL import Image  # noqa: E402


def _jitter(png, k, amp=4):
    """`png` with small per-pixel noise that differs per call `k` (a video
    frame playing on an otherwise still view)."""
    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    arr += np.random.default_rng(1000 + k).integers(-amp, amp + 1, arr.shape)
    buf = io.BytesIO()
    Image.fromarray(np.clip(arr, 0, 255).astype("uint8")).save(buf, format="PNG")
    return buf.getvalue()


def test_verified_top_stabilises_on_jittering_video(phone, monkeypatch):
    # R22: md5 never repeats on a playing video; the thumbnail near-match does.
    phone.pos = 2
    monkeypatch.setattr(adb, "screenshot",
                        lambda: _jitter(phone.screenshot(), phone.shots))

    top = main.scroll_to_verified_top()

    assert top is not None and phone.pos == 0
    assert phone.scroll_ups == 3
    a, b = main._profile_region_hash(top), main._profile_region_hash(screen(1, 0))
    assert a != b            # really jittered: md5 equality would never settle


def test_verified_top_never_settles_while_view_keeps_changing(phone, monkeypatch):
    monkeypatch.setattr(adb, "screenshot",
                        lambda: screen(600 + phone.scroll_ups, 0))

    assert main.scroll_to_verified_top() is None
    assert phone.scroll_ups == config.FRAMES_PER_PROFILE * 2 + 4


def test_like_confirmed_when_next_profile_is_a_video(phone, monkeypatch):
    real = phone.screenshot
    monkeypatch.setattr(adb, "screenshot",
                        lambda: _jitter(real(), phone.shots) if phone.sent else real())

    assert main.do_like("hi", phone.ident) == "liked_confirmed"


def test_capture_retries_failed_dump_once_before_scrolling(phone, monkeypatch):
    # R23: first dump fails, retry ~1 s later (still at frames[0]) succeeds.
    monkeypatch.setattr(config, "FRAMES_PER_PROFILE", 2)
    phone.pre_send_dumps = [ui.UiDumpFailed("busy"), None]
    at_dump = []
    real = phone.ui_texts
    monkeypatch.setattr(ui, "ui_texts", lambda: at_dump.append(
        (phone.scroll_downs, main.time.monotonic())) or real())

    frames, ident = main.capture_profile()

    assert ident.skip_label == "Skip Sam"
    assert [d[0] for d in at_dump] == [0, 0]          # both before scroll_down
    assert at_dump[1][1] - at_dump[0][1] >= 1.0       # slept ~1 s
    assert main.do_like("hi", ident) == "liked_confirmed"


def test_capture_retries_dump_without_skip_label(phone, monkeypatch):
    monkeypatch.setattr(config, "FRAMES_PER_PROFILE", 2)
    phone.pre_send_dumps = [DISCOVER, None]

    _, ident = main.capture_profile()

    assert ident.skip_label == "Skip Sam" and phone.pre_send_dump_count == 2


def test_capture_no_retry_when_first_dump_has_label(phone, monkeypatch):
    monkeypatch.setattr(config, "FRAMES_PER_PROFILE", 2)

    main.capture_profile()

    assert phone.pre_send_dump_count == 1


ROSE_AT_SEND = ui.UiNode("Send a Rose with message", 600, 1100, 820, 1240)


def test_send_point_inside_rose_refused_without_send(phone):
    # R24: fresh dump right before the Send Like tap.
    phone.pre_send_dumps = [None, nodes_for("Sam") + [ROSE_AT_SEND]]

    with pytest.raises(ui.UnsafeTap, match="send_like"):
        main.do_like("hi", phone.ident)
    assert not phone.sent and SEND not in [t[:2] for t in phone.taps]


def test_send_point_inside_rose_halts_via_attempt_like(phone, monkeypatch):
    monkeypatch.setattr(main, "recover_from_failed_like", lambda: pytest.fail("recovered"))
    phone.pre_send_dumps = [None, nodes_for("Sam") + [ROSE_AT_SEND]]
    decision = type("D", (), {"message": "hi", "anchor": None})()

    with pytest.raises(main.Halt, match="unsafe_tap"):
        main._attempt_like(1, decision, screen(1, 0), phone.ident)
    assert not phone.sent


def test_send_guard_dump_failure_halts_without_send(phone):
    phone.pre_send_dumps = [None, ui.UiDumpFailed("busy")]

    with pytest.raises(main.Halt) as e:
        main.do_like("hi", phone.ident)
    assert e.value.reason == "send_guard_unreadable"
    assert not phone.sent and phone.typed_text == ["hi"]


def test_send_guard_runs_after_typing(phone, monkeypatch):
    order = []
    real_dump, real_type, real_tap = phone.ui_texts, phone.input_text, phone.tap
    monkeypatch.setattr(ui, "ui_texts", lambda: order.append("dump") or real_dump())
    monkeypatch.setattr(adb, "input_text", lambda t: order.append("type") or real_type(t))
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None:
                        order.append(target) or real_tap(x, y, target))

    assert main.do_like("hi", phone.ident) == "liked_confirmed"
    i = order.index("send_like")
    assert order[i - 2:i] == ["type", "dump"]


def test_post_send_screen_must_be_discover(phone):
    # R26: the final read needs "Skip <name>" or "Discover".
    phone.ui_sequence = [DISCOVER, DISCOVER, [ui.UiNode("Signals", 148, 110, 338, 196)]]

    with pytest.raises(main.LikeUnconfirmed, match="post-send screen not Discover"):
        main.do_like("hi", phone.ident)


def test_post_send_skip_label_alone_is_enough(phone):
    phone.ui_sequence = [DISCOVER, DISCOVER, nodes_for("Alex")[1:]]

    assert main.do_like("hi", phone.ident) == "liked_confirmed"
