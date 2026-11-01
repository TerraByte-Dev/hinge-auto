"""adb.py device targeting. No real device needed: subprocess.run is faked."""

import subprocess

import pytest

import adb
import config


class FakeRun:
    def __init__(self, stdout: bytes = b""):
        self.calls: list[list[str]] = []
        self.stdout = stdout

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout)


DEVICES_OUT = (
    b"List of devices attached\n"
    b"emulator-5554\tdevice\n"
    b"RZCX920XP8L\tdevice\n\n"
)


def test_tap_targets_configured_serial(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "ADB_SERIAL", "RZCX920XP8L")

    adb.tap(10, 20)

    assert fake.calls == [
        ["adb", "-s", "RZCX920XP8L", "shell", "input", "tap", "10", "20"]
    ]


def test_tap_without_serial_uses_default_device(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "ADB_SERIAL", None)

    adb.tap(10, 20)

    assert fake.calls == [["adb", "shell", "input", "tap", "10", "20"]]


def test_check_device_returns_configured_serial_when_attached(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(DEVICES_OUT))
    monkeypatch.setattr(config, "ADB_SERIAL", "RZCX920XP8L")

    assert adb.check_device() == "RZCX920XP8L"


def test_check_device_raises_when_configured_serial_missing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(DEVICES_OUT))
    monkeypatch.setattr(config, "ADB_SERIAL", "NOTHERE123")

    with pytest.raises(RuntimeError, match="NOTHERE123"):
        adb.check_device()


IME_SHOWN = b"  mInputShown=true\n  mIsInputViewShown=true\n"
IME_HIDDEN = b"  mInputShown=false\n  mIsInputViewShown=false\n"


def test_hide_keyboard_presses_back_when_keyboard_up(monkeypatch):
    fake = FakeRun(IME_SHOWN)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "ADB_SERIAL", None)

    assert adb.hide_keyboard() is True
    assert ["adb", "shell", "input", "keyevent", "KEYCODE_BACK"] in fake.calls


def test_hide_keyboard_never_presses_back_when_keyboard_down(monkeypatch):
    # Back with no keyboard up navigates out of Hinge.
    fake = FakeRun(IME_HIDDEN)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "ADB_SERIAL", None)

    assert adb.hide_keyboard() is False
    assert not any("KEYCODE_BACK" in c for c in fake.calls)


@pytest.mark.real_adb_binary
def test_adb_binary_prefers_path(monkeypatch):
    adb._adb_binary.cache_clear()
    monkeypatch.setattr(adb.shutil, "which", lambda name: "/usr/bin/adb")

    assert adb._adb_binary() == "/usr/bin/adb"


@pytest.mark.real_adb_binary
def test_adb_binary_falls_back_to_sdk(monkeypatch, tmp_path):
    adb._adb_binary.cache_clear()
    fallback = tmp_path / "adb"
    fallback.write_text("")
    monkeypatch.setattr(adb.shutil, "which", lambda name: None)
    monkeypatch.setattr(adb, "ADB_FALLBACK", fallback)

    assert adb._adb_binary() == str(fallback)


@pytest.mark.real_adb_binary
def test_adb_binary_missing_raises(monkeypatch, tmp_path):
    adb._adb_binary.cache_clear()
    monkeypatch.setattr(adb.shutil, "which", lambda name: None)
    monkeypatch.setattr(adb, "ADB_FALLBACK", tmp_path / "platform-tools/adb")

    with pytest.raises(RuntimeError, match="platform-tools/adb"):
        adb._adb_binary()
