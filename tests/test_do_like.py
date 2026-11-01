"""main.do_like: typed-text guard, post-send confirmation, Rose sheet."""

import pytest

import adb
import config
import main
import ui
import vision
from tests.test_ui import ROSE_SHEET_XML

SEND = (708, 1168)
ROSE_SHEET = ui.parse_ui_xml(ROSE_SHEET_XML)
DISCOVER = [ui.UiNode("Signals", 148, 110, 338, 196)]
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
    """Screens are b"before" until Send Like is tapped, then b"after"."""

    def __init__(self):
        self.taps: list[tuple[int, int, str | None]] = []
        self.sent = False
        self.ui_sequence: list = [DISCOVER]
        self.advance = True          # does the profile change after send?
        self.send_after = None       # Send pill position on the after-screen
        self.typed = False
        self.text_lands = True
        self.typed_pixels = 9999     # dark pixels in the comment box once typed
        self.empty_pixels = 40       # ... before typing
        self.like_photo = None       # heart point from the "Like photo" label

    def text_pixels(self, png, xy):
        if self.typed and self.text_lands:
            return self.typed_pixels
        return self.empty_pixels

    def screenshot(self):
        return b"after" if self.sent else b"before"

    def tap(self, x, y, target=None):
        self.taps.append((x, y, target))
        if (x, y) == SEND:
            self.sent = True

    def ui_texts(self):
        item = self.ui_sequence.pop(0) if len(self.ui_sequence) > 1 else self.ui_sequence[0]
        if isinstance(item, Exception):
            raise item
        return item

    def region_hash(self, png):
        if png == b"after" and not self.advance:
            return "before"
        return png.decode()

    def find_send_like(self, png):
        return SEND if png == b"before" else self.send_after


@pytest.fixture
def phone(monkeypatch):
    p = FakePhone()
    clock = FakeClock()
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(main, "scroll_back_to_top", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(main.time, "sleep", clock.sleep)
    monkeypatch.setattr(main, "_profile_region_hash", p.region_hash)
    monkeypatch.setattr(adb, "jitter_sleep", lambda key: None)
    monkeypatch.setattr(adb, "screenshot", p.screenshot)
    monkeypatch.setattr(adb, "tap", p.tap)
    monkeypatch.setattr(adb, "input_text", lambda text: setattr(p, "typed", True))
    monkeypatch.setattr(ui, "ui_texts", p.ui_texts)
    monkeypatch.setattr(vision, "find_first_heart", lambda png: (917, 1388))
    monkeypatch.setattr(vision, "find_send_like", p.find_send_like)
    monkeypatch.setattr(vision, "comment_field_text_pixels", p.text_pixels)
    monkeypatch.setattr(main, "_like_photo_xy", lambda: p.like_photo, raising=False)
    return p


def _taps_after_send(p):
    i = [t[:2] for t in p.taps].index(SEND)
    return p.taps[i + 1:]


def test_do_like_aborts_instead_of_sending_when_text_missing(phone):
    phone.text_lands = False

    with pytest.raises(main.LikeAborted) as e:
        main.do_like("hello there")

    assert e.value.outcome == "like_aborted_text"
    assert not phone.sent


def test_missing_heart_is_detection_abort(phone, monkeypatch):
    monkeypatch.setattr(vision, "find_first_heart", lambda png: None)

    with pytest.raises(main.LikeAborted) as e:
        main.do_like("hi")

    assert e.value.outcome == "like_aborted_detection"


def test_confirmed_when_no_sheet_and_profile_advanced(phone):
    assert main.do_like("hi") == "liked_confirmed"


def test_rose_sheet_taps_send_like_anyway(phone):
    phone.ui_sequence = [ROSE_SHEET, DISCOVER]

    assert main.do_like("hi") == "like_sheet_handled"
    assert _taps_after_send(phone) == [(539, 2040, "send_like_anyway")]


def test_rose_sheet_persisting_raises_unconfirmed(phone):
    phone.ui_sequence = [ROSE_SHEET, ROSE_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="rose sheet persisted"):
        main.do_like("hi")


def test_unknown_sheet_raises_unconfirmed_without_tapping(phone):
    phone.ui_sequence = [OTHER_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="unknown sheet"):
        main.do_like("hi")
    assert _taps_after_send(phone) == []


def test_sheet_appearing_after_rose_sheet_is_unconfirmed(phone):
    phone.ui_sequence = [ROSE_SHEET, OTHER_SHEET]

    with pytest.raises(main.LikeUnconfirmed, match="unknown sheet"):
        main.do_like("hi")


def test_ui_dump_failure_after_send_is_unconfirmed(phone):
    phone.ui_sequence = [ui.UiDumpFailed("busy")]

    with pytest.raises(main.LikeUnconfirmed, match="screen unknown"):
        main.do_like("hi")


def test_profile_not_advanced_raises_unconfirmed(phone):
    phone.advance = False

    with pytest.raises(main.LikeUnconfirmed, match="did not advance"):
        main.do_like("hi")


def test_send_pill_still_visible_raises_unconfirmed(phone):
    phone.send_after = SEND

    with pytest.raises(main.LikeUnconfirmed, match="Send Like still visible"):
        main.do_like("hi")


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
        main.do_like("hi")


def test_post_send_frame_is_saved(phone, monkeypatch):
    import runlog
    saved = []
    monkeypatch.setattr(runlog, "save_frame", lambda name, png: saved.append((name, png)))

    main.do_like("hi")

    assert ("post_send", b"after") in saved


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

    assert main.do_like("hi") == "liked_confirmed"
    assert phone.taps[0][:2] == (918, 1350)


def test_long_opener_in_scrolling_box_is_accepted(phone):
    # Run 20261101-151639 profile 4: the S23 comment box scrolls, so a
    # 76-char opener shows ~2438 dark pixels however long it is. Readings
    # replayed from the run: 2581 before typing, 2438 after.
    phone.empty_pixels = 2581
    phone.typed_pixels = 2438

    assert main.do_like("the shop more clothes goal is so valid -- what style "
                        "are you building toward?") == "liked_confirmed"
