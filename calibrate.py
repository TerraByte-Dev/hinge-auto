"""Saves a screenshot so you can read pixel coords for config.COORDS.

Open the resulting PNG in any image viewer that shows cursor position
(MS Paint, IrfanView, etc.) and hover over each UI element you need
to record.
"""

from pathlib import Path

import adb
import config
import ui


def main() -> None:
    adb.check_device()
    path = Path(__file__).parent / "calibrate.png"
    path.write_bytes(adb.screenshot())
    print(f"Saved: {path}")
    print()
    print("Open the file and read pixel coords for:")
    print("  1. Skip (X) button         -> COORDS['skip_button']")
    print("  2. Heart on first photo    -> COORDS['heart_photo_1']")
    print("     (scroll Hinge to the top before screenshotting)")
    print("  3. 'Send Like' button      -> COORDS['send_like_button']")
    print("     (tap a heart first to open compose, then re-run this script)")
    print()
    print(f"Then edit the '{config.DEVICE}' profile in config.DEVICES.")
    print()
    print(f"Active profile '{config.DEVICE}' ({config.SCREEN_WIDTH}x{config.SCREEN_HEIGHT}):")
    for k, v in config.DEVICES[config.DEVICE].get("coords", {}).items():
        print(f"  {k:<18} {v}")
    print()
    print("On-screen labels (center = tap point), fastest way to read coords:")
    try:
        for n in ui.ui_texts():
            print(f"  {str(n.center):<14} {n.label[:70]}")
    except ui.UiDumpFailed as e:
        print(f"  (label dump failed: {e})")


if __name__ == "__main__":
    main()
