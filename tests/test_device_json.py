"""config.load_device_json refuses coord files made for another screen."""

import json

import pytest

import config


@pytest.fixture
def s23(monkeypatch):
    monkeypatch.setattr(config, "SCREEN_WIDTH", 1080)
    monkeypatch.setattr(config, "SCREEN_HEIGHT", 2340)


def _write(path, screen):
    path.write_text(json.dumps({"screen_size": screen, "age_chip": [1, 2]}))
    return path


def test_load_device_json_ok(s23, tmp_path):
    data = config.load_device_json(_write(tmp_path / "f.json", [1080, 2340]), "run x")

    assert data["age_chip"] == [1, 2]


def test_load_device_json_wrong_screen(s23, tmp_path):
    path = _write(tmp_path / "f.json", [1080, 2424])

    with pytest.raises(RuntimeError, match=r"1080x2424.*1080x2340"):
        config.load_device_json(path, "run x")


def test_load_device_json_missing(s23, tmp_path):
    with pytest.raises(RuntimeError, match="python calibrate_filters.py"):
        config.load_device_json(tmp_path / "nope.json", "python calibrate_filters.py")
