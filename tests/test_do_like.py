"""main.do_like never taps Send Like when the typed opener didn't land."""

import pytest

import adb
import config
import main
import vision


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_do_like_aborts_instead_of_sending_when_text_missing(monkeypatch):
    taps = []
    clock = FakeClock()
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(main, "scroll_back_to_top", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(main.time, "sleep", clock.sleep)
    monkeypatch.setattr(adb, "jitter_sleep", lambda key: None)
    monkeypatch.setattr(adb, "screenshot", lambda: b"png")
    monkeypatch.setattr(adb, "tap", lambda x, y: taps.append((x, y)))
    monkeypatch.setattr(adb, "input_text", lambda text: None)
    monkeypatch.setattr(vision, "find_first_heart", lambda png: (917, 1388))
    monkeypatch.setattr(vision, "find_send_like", lambda png: (708, 1168))
    monkeypatch.setattr(vision, "comment_field_text_pixels", lambda png, xy: 40)

    with pytest.raises(RuntimeError, match="typed text"):
        main.do_like("hello there")

    assert (708, 1168) not in taps


def test_failed_like_recovery_hides_keyboard_before_skipping(monkeypatch):
    events = []
    monkeypatch.setattr(adb, "jitter_sleep", lambda key: None)
    monkeypatch.setattr(adb, "hide_keyboard", lambda: events.append("hide") or True)
    monkeypatch.setattr(adb, "tap", lambda x, y: events.append(("tap", x, y)))

    main.recover_from_failed_like()

    assert events == ["hide", ("tap", *config.COORDS["skip_button"])]
