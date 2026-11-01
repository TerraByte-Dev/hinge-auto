"""Thin wrapper around `adb` commands."""

import random
import shlex
import subprocess
import time

import config


def _run(args: list[str], capture: bool = False) -> bytes | None:
    cmd = ["adb"]
    if config.ADB_SERIAL:
        cmd += ["-s", config.ADB_SERIAL]
    cmd += args
    if capture:
        result = subprocess.run(cmd, capture_output=True, check=True)
        return result.stdout
    subprocess.run(cmd, check=True)
    return None


def check_device() -> str:
    """Raise if no device is connected. Return the serial of the target device
    (config.ADB_SERIAL if set, else the first device)."""
    out = _run(["devices"], capture=True).decode()
    lines = [l for l in out.splitlines()[1:] if l.strip() and "\tdevice" in l]
    serials = [l.split("\t")[0] for l in lines]
    if config.ADB_SERIAL:
        if config.ADB_SERIAL not in serials:
            raise RuntimeError(
                f"ADB device {config.ADB_SERIAL} not found (attached: "
                f"{serials or 'none'}). Plug in the phone and accept the "
                "USB debugging prompt."
            )
        return config.ADB_SERIAL
    if not serials:
        raise RuntimeError(
            "No ADB device found. Start your emulator and run `adb devices`."
        )
    return serials[0]


def screenshot() -> bytes:
    """Return PNG bytes of the current screen."""
    return _run(["exec-out", "screencap", "-p"], capture=True)


def tap(x: int, y: int) -> None:
    _run(["shell", "input", "tap", str(x), str(y)])


def input_text(text: str) -> None:
    """Type `text` into the currently focused field via `adb shell input text`.

    Single-quotes the payload with shlex so $, `, ", and spaces survive the
    device shell. Caller should keep text plain ASCII — emoji and the chars
    \\ " $ ` should be filtered upstream (the model is instructed to avoid
    them) since `input text` itself only sends keyevents.
    """
    quoted = shlex.quote(text)
    _run(["shell", f"input text {quoted}"])


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    _run(["shell", "input", "swipe",
          str(x1), str(y1), str(x2), str(y2), str(duration_ms)])


def scroll_down() -> None:
    c = config.COORDS
    swipe(*c["scroll_from"], *c["scroll_to"], c["scroll_duration_ms"])


def scroll_up() -> None:
    c = config.COORDS
    swipe(*c["scroll_to"], *c["scroll_from"], c["scroll_duration_ms"])


def jitter_sleep(key: str) -> None:
    lo, hi = config.DELAYS[key]
    time.sleep(random.uniform(lo, hi))
