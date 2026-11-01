"""locations.is_out_of_candidates: the phone's dark heart isn't the empty state."""

import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

import adb
import config
import locations
import main
import runlog
import ui
from tests.test_vision import _new_ui_frame, _png


def _empty_state_frame():
    img = Image.new("RGB", (1080, 2340), (255, 255, 255))
    ImageDraw.Draw(img).rounded_rectangle((100, 1420, 980, 1530), 50, fill=(26, 26, 26))
    return _png(img)


@pytest.fixture
def phone(monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    monkeypatch.setattr(locations, "EMPTY_STATE_LABEL", "You've seen everyone")


def test_heart_in_patch_is_not_out_of_candidates_on_phone(phone):
    png = _png(_new_ui_frame((850, 1475)))
    assert locations.is_out_of_candidates(png, nodes=[]) is False


def test_out_of_candidates_with_label_on_phone(phone):
    nodes = [ui.UiNode("You've seen everyone", 100, 1000, 980, 1100)]

    assert locations.is_out_of_candidates(_empty_state_frame(), nodes=nodes) is True


def test_phone_reads_labels_when_pixels_match(phone, monkeypatch):
    monkeypatch.setattr(ui, "ui_texts", lambda: [ui.UiNode("You've seen everyone", 0, 0, 1, 1)])

    assert locations.is_out_of_candidates(_empty_state_frame()) is True


def test_phone_skips_label_read_when_pixels_dont_match(phone, monkeypatch):
    monkeypatch.setattr(ui, "ui_texts", lambda: pytest.fail("dumped labels"))

    assert locations.is_out_of_candidates(_png(Image.new("RGB", (1080, 2340), "white"))) is False


def test_emulator_behavior_unchanged(monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")

    assert locations.is_out_of_candidates(_empty_state_frame()) is True


def test_rotate_refuses_without_label(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["main.py", "--rotate", "nyc_area"])
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(locations, "EMPTY_STATE_LABEL", None)
    monkeypatch.setattr(adb, "check_device", lambda: "S")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "capture_profile", lambda: pytest.fail("loop ran"))

    try:
        assert main.main() == 2
    finally:
        runlog.end_run()
    (run,) = tmp_path.iterdir()
    assert "EMPTY_STATE_LABEL" in (run / "summary.json").read_text()
