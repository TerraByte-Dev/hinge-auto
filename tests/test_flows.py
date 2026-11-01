"""Each scripted flow checks it reached the expected screen right after its
first navigation tap, and taps nothing further when it didn't."""

import collections

import pytest

import adb
import config
import filters
import locations
import matches_scan
import scan_self
import ui


@pytest.fixture
def taps(monkeypatch):
    out = []
    monkeypatch.setattr(adb, "tap", lambda x, y, target=None: out.append((x, y)))
    monkeypatch.setattr(adb, "swipe", lambda *a, **k: out.append(("swipe",)))
    monkeypatch.setattr(adb, "input_text", lambda t: out.append(("text",)))
    monkeypatch.setattr(adb, "jitter_sleep", lambda k: None)
    monkeypatch.setattr(adb, "screenshot", lambda: b"x")
    for mod in (filters, locations, matches_scan, scan_self):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return out


@pytest.fixture
def wrong_screen(monkeypatch):
    calls = []

    def require(expected, step):
        calls.append((expected, step))
        raise ui.LayoutChanged(("expected",), step, ["Discover"])

    monkeypatch.setattr(ui, "require_label", require)
    return calls


def test_location_checks_preferences_after_sliders_tap(taps, wrong_screen, monkeypatch):
    coords = {k: [i, i] for i, k in enumerate(
        ["sliders_icon", "neighborhood_row", "text_input", "input_clear_x",
         "first_suggestion", "back_arrow", "preferences_close", "discover_tab"], 1)}
    coords["delays"] = collections.defaultdict(float)
    monkeypatch.setattr(locations, "load_coords", lambda: coords)
    monkeypatch.setattr(locations, "resolve", lambda n: n)

    with pytest.raises(ui.LayoutChanged):
        locations.set_location("brooklyn")

    assert wrong_screen[0][0] == locations.NEIGHBORHOOD_LABEL
    assert taps[-1] == (1, 1)  # last tap was sliders_icon


def test_filters_check_age_sheet_after_chip_tap(taps, wrong_screen, monkeypatch):
    cal = {"age_anchor": 30, "x_anchor": 500, "slope_px_per_year": 10}
    monkeypatch.setattr(filters, "load_coords", lambda: {
        "age_chip": [7, 7], "slider_y": 1700, "left_thumb": cal, "right_thumb": cal,
        "min_age": 18, "max_age": 50, "apply_button": [8, 8], "dismiss_tap": [9, 9]})

    with pytest.raises(ui.LayoutChanged):
        filters.set_age_range(25, 35)

    assert wrong_screen[0][0] == filters.AGE_SHEET_LABEL and taps == [(7, 7)]


def test_scan_self_checks_view_tab_after_avatar(taps, wrong_screen):
    with pytest.raises(ui.LayoutChanged):
        scan_self.navigate_to_self_view()

    assert wrong_screen[0][0] == scan_self.VIEW_LABEL
    assert taps == [tuple(config.COORDS["nav_self_pfp"]), tuple(config.COORDS["self_avatar"])]


def test_matches_checks_list_after_tab_tap(taps, wrong_screen, monkeypatch):
    monkeypatch.setattr(matches_scan, "_load_coords", lambda: {"matches_tab": [5, 5]})

    with pytest.raises(ui.LayoutChanged):
        matches_scan.open_matches_tab()

    assert wrong_screen[0][0] == matches_scan.MATCHES_LABELS and taps == [(5, 5)]


def test_location_taps_neighbourhood_row_by_label(taps, monkeypatch):
    coords = {k: [i, i] for i, k in enumerate(
        ["sliders_icon", "neighborhood_row", "text_input", "input_clear_x",
         "first_suggestion", "back_arrow", "preferences_close", "discover_tab"], 1)}
    coords["delays"] = collections.defaultdict(float)
    monkeypatch.setattr(locations, "load_coords", lambda: coords)
    monkeypatch.setattr(locations, "resolve", lambda n: n)
    # Banner dismissed -> rows moved up; the label says where the row is now.
    monkeypatch.setattr(ui, "require_label", lambda expected, step: [
        ui.UiNode("My neighbourhood", 48, 970, 413, 1023)])

    locations.set_location("brooklyn")

    assert taps[2] == (230, 996)  # after discover_tab, sliders_icon


def test_scan_self_return_uses_device_back_taps(taps, monkeypatch):
    monkeypatch.setattr(config, "SELF_VIEW_BACK_TAPS", 1)

    scan_self.return_to_discover()

    back = tuple(config.COORDS["back_arrow"])
    assert taps == [back, tuple(config.COORDS["nav_discover"])]


def test_s23_label_constants_captured():
    assert locations.NEIGHBORHOOD_LABEL == "My neighbourhood"
    assert filters.AGE_SHEET_LABEL == "Age"
    assert scan_self.VIEW_LABEL == "View"
    assert "No matches right now" in matches_scan.MATCHES_LABELS
