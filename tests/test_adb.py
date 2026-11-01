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
