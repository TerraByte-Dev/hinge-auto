"""ui.py: uiautomator label parsing and Rose-safe label taps."""

import pytest

import adb
import ui

ROSE_SHEET_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
 <node text="" content-desc="Close sheet" bounds="[0,0][1080,2340]">
  <node text="Send a Rose instead?" content-desc="" bounds="[148,1326][933,1434]" />
  <node text="Upgrade your Like to a Rose to be seen first." content-desc="" bounds="[102,1482][978,1688]" />
  <node text="" content-desc="" bounds="[333,1790][746,1934]">
   <node text="Send a Rose" content-desc="" bounds="[405,1832][674,1891]" />
  </node>
  <node text="" content-desc="" bounds="[273,1969][806,2113]">
   <node text="Send Like anyway" content-desc="" bounds="[345,2011][734,2070]" />
  </node>
 </node>
</hierarchy>"""


def test_parse_extracts_text_and_desc():
    labels = [n.label for n in ui.parse_ui_xml(ROSE_SHEET_XML)]

    assert labels == [
        "Close sheet", "Send a Rose instead?",
        "Upgrade your Like to a Rose to be seen first.",
        "Send a Rose", "Send Like anyway",
    ]


def test_parse_empty_raises():
    with pytest.raises(ui.UiDumpFailed):
        ui.parse_ui_xml("")


def test_parse_garbage_raises():
    with pytest.raises(ui.UiDumpFailed):
        ui.parse_ui_xml("ERROR: could not get idle state.")


def test_find_label_exact_only():
    nodes = ui.parse_ui_xml(ROSE_SHEET_XML)

    assert ui.find_label(nodes, "Send Like") is None
    assert ui.find_label(nodes, "Send Like anyway").center == (539, 2040)


def test_safe_tap_taps_send_like_anyway_center(monkeypatch):
    taps = []
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None: taps.append((x, y, target)))

    xy = ui.safe_tap_label(ui.parse_ui_xml(ROSE_SHEET_XML), "Send Like anyway", "send_like_anyway")

    assert xy == (539, 2040)
    assert taps == [(539, 2040, "send_like_anyway")]


def test_safe_tap_refuses_point_inside_rose(monkeypatch):
    taps = []
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None: taps.append((x, y)))
    shifted = ROSE_SHEET_XML.replace("[345,2011][734,2070]", "[420,1840][660,1880]")

    with pytest.raises(ui.UnsafeTap):
        ui.safe_tap_label(ui.parse_ui_xml(shifted), "Send Like anyway", "send_like_anyway")
    assert taps == []


def test_safe_tap_missing_label_raises(monkeypatch):
    monkeypatch.setattr(adb, "tap", lambda *a, **k: pytest.fail("tapped"))

    with pytest.raises(ui.UnsafeTap):
        ui.safe_tap_label([], "Send Like anyway", "send_like_anyway")


def test_ui_texts_retries_then_raises(monkeypatch):
    calls = []

    def fake_run(args, capture=False):
        calls.append(args)
        return b""

    monkeypatch.setattr(adb, "_run", fake_run)
    monkeypatch.setattr(ui.time, "sleep", lambda s: None)

    with pytest.raises(ui.UiDumpFailed):
        ui.ui_texts()
    assert sum(1 for a in calls if "uiautomator" in a) == 2


def test_ui_texts_parses_dump(monkeypatch):
    def fake_run(args, capture=False):
        return ROSE_SHEET_XML.encode() if "cat" in args else b""

    monkeypatch.setattr(adb, "_run", fake_run)

    assert ui.find_label(ui.ui_texts(), "Send a Rose") is not None
