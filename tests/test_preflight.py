"""main.preflight: layout check before the loop touches anything."""

import sys

import pytest
from PIL import ImageDraw

import adb
import config
import main
import runlog
import ui
from tests.test_vision import _new_ui_frame, _png

SKIP = (152, 1865)
SKIP_LABEL = [ui.UiNode("Skip Ana", 72, 1785, 232, 1945)]
SHEET = SKIP_LABEL + [ui.UiNode("Close sheet", 0, 0, 1080, 2340)]


def _frame(skip_at=SKIP, heart=True):
    img = _new_ui_frame((917, 1387)) if heart else _new_ui_frame((100, 300))
    if not heart:
        ImageDraw.Draw(img).rectangle((0, 200, 1080, 400), fill=(245, 245, 245))
    x, y = skip_at
    d = ImageDraw.Draw(img)
    d.ellipse((x - 80, y - 80, x + 80, y + 80), fill=(255, 255, 255))
    d.line((x - 30, y - 30, x + 30, y + 30), fill=(0, 0, 0), width=12)
    d.line((x - 30, y + 30, x + 30, y - 30), fill=(0, 0, 0), width=12)
    return _png(img)


@pytest.fixture
def fingerprint(monkeypatch):
    monkeypatch.setitem(config.COORDS, "skip_button", SKIP)
    fp = main.skip_patch_counts(_frame())
    monkeypatch.setattr(config, "SKIP_FINGERPRINT", fp)
    return fp


def test_skip_patch_counts_sees_x_and_circle(fingerprint):
    assert fingerprint["dark"] > 500 and fingerprint["white"] > 5000


def test_preflight_passes_on_good_frame(fingerprint):
    assert main.preflight(_frame(), SKIP_LABEL) == []


def test_preflight_flags_moved_skip_button(fingerprint):
    reasons = main.preflight(_frame(skip_at=(152, 1500)), SKIP_LABEL)

    assert any("skip" in r for r in reasons)


def test_preflight_flags_missing_heart(fingerprint):
    reasons = main.preflight(_frame(heart=False), SKIP_LABEL)

    assert any("heart" in r for r in reasons)


def test_preflight_flags_open_sheet(fingerprint):
    reasons = main.preflight(_frame(), SHEET)

    assert any("sheet" in r for r in reasons)


def test_preflight_without_fingerprint_skips_that_check(monkeypatch):
    monkeypatch.setattr(config, "SKIP_FINGERPRINT", None)

    assert main.preflight(_frame(skip_at=(152, 1500)), SKIP_LABEL) == []


def test_preflight_flags_missing_skip_label_on_phone(fingerprint, monkeypatch):
    """2026-11-03: uiautomator returned a label-less tree, so every like
    became a no_identity skip. Refuse to start instead."""
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")

    reasons = main.preflight(_frame(), [])

    assert any("Skip <name>" in r for r in reasons)


def test_preflight_skip_label_not_required_on_emulator(fingerprint, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")

    assert main.preflight(_frame(), []) == []


def test_s23_profile_has_measured_fingerprint():
    assert config.DEVICES["galaxy_s23"]["skip_fingerprint"] == {"dark": 1657, "white": 12500}


def test_main_exits_2_when_preflight_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(adb, "check_device", lambda: "S")
    monkeypatch.setattr(adb, "screenshot", lambda: b"x")
    monkeypatch.setattr(ui, "ui_texts", lambda: [])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "preflight", lambda png, nodes: ["skip X not where expected"])
    monkeypatch.setattr(main, "capture_profile", lambda: pytest.fail("loop ran"))

    try:
        assert main.main() == 2
    finally:
        runlog.end_run()
    (run,) = tmp_path.iterdir()
    assert "preflight" in (run / "summary.json").read_text()
