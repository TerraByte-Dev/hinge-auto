"""Screen-state checks via the Android accessibility tree (uiautomator).

`vision.py` finds elements by pixels on every frame; this module reads the
on-screen *labels* and their bounds. It costs ~1-2s per dump, so it is used
only at checkpoints (after Send Like, preflight, scripted-flow screens),
never per frame.

The one rule that matters here: sheet buttons are only ever tapped through
`safe_tap_label`, which refuses any point inside a Rose element. Roses are
paid and must never be sent.
"""

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import adb

_DUMP_PATH = "/sdcard/hinge_ui.xml"


class UiDumpFailed(RuntimeError):
    """uiautomator returned nothing usable (it errors while animations run).
    Callers must treat this as 'screen unknown', never as 'nothing there'."""


class UnsafeTap(RuntimeError):
    """Refused to tap: label missing, or its center sits on a Rose element."""


@dataclass(frozen=True)
class UiNode:
    label: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


def _bounds(s: str) -> tuple[int, int, int, int]:
    # "[x0,y0][x1,y1]"
    a, b = s.strip("[]").split("][")
    x0, y0 = (int(v) for v in a.split(","))
    x1, y1 = (int(v) for v in b.split(","))
    return x0, y0, x1, y1


def parse_ui_xml(xml: str) -> list[UiNode]:
    """One UiNode per non-empty `text` / `content-desc`, in document order."""
    if not xml or not xml.strip():
        raise UiDumpFailed("empty uiautomator dump")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise UiDumpFailed(f"unparsable uiautomator dump: {xml[:80]!r}") from e
    nodes = []
    for el in root.iter("node"):
        bounds = el.get("bounds")
        if not bounds:
            continue
        for attr in ("content-desc", "text"):
            label = (el.get(attr) or "").strip()
            if label:
                nodes.append(UiNode(label, *_bounds(bounds)))
    return nodes


def ui_texts() -> list[UiNode]:
    """Dump and parse the current screen's labels. One retry after 1s."""
    last: Exception | None = None
    for attempt in range(2):
        if attempt:
            time.sleep(1.0)
        try:
            adb._run(["shell", "uiautomator", "dump", _DUMP_PATH], capture=True)
            xml = adb._run(["exec-out", "cat", _DUMP_PATH], capture=True)
            return parse_ui_xml((xml or b"").decode("utf-8", "replace"))
        except Exception as e:  # subprocess errors and UiDumpFailed alike
            last = e
    raise UiDumpFailed(f"uiautomator dump failed twice: {last}") from last


def find_label(nodes: list[UiNode], text: str) -> UiNode | None:
    """Exact, case-sensitive match on a node's label."""
    for n in nodes:
        if n.label == text:
            return n
    return None


def _is_rose_node(n: UiNode) -> bool:
    # "Send a Rose" button text counts; the "Send a Rose instead?" sheet
    # title is a question, not a control.
    return "Rose" in n.label and not n.label.endswith("?")


def safe_tap_label(nodes: list[UiNode], text: str, target: str) -> tuple[int, int]:
    """Tap the center of the node labelled `text`, unless that point lies on
    a Rose element. Returns the tapped point."""
    node = find_label(nodes, text)
    if node is None:
        raise UnsafeTap(f"label {text!r} not on screen")
    x, y = node.center
    for n in nodes:
        if _is_rose_node(n) and n.contains(x, y):
            raise UnsafeTap(f"{text!r} center {x, y} lies inside {n.label!r}")
    adb.tap(x, y, target=target)
    return x, y


class LayoutChanged(RuntimeError):
    """A scripted flow isn't on the screen it expected after navigating."""

    def __init__(self, expected: tuple[str, ...], step: str, seen: list[str]):
        super().__init__(f"{step}: expected one of {list(expected)} on screen; "
                         f"saw {seen}")
        self.expected, self.step, self.seen = expected, step, seen


def require_label(expected: str | tuple[str, ...] | None, step: str) -> list[UiNode]:
    """Confirm one of `expected` labels is on screen; return the nodes.

    `expected` None means the label hasn't been captured on this device yet:
    the check is logged as skipped and the flow continues as before.
    """
    import runlog
    if expected is None:
        runlog.event("layout_check", target=step, result="skipped_unmeasured")
        return []
    wanted = (expected,) if isinstance(expected, str) else tuple(expected)
    try:
        nodes = ui_texts()
    except UiDumpFailed as e:
        raise LayoutChanged(wanted, step, [f"<dump failed: {e}>"]) from e
    seen = [n.label for n in nodes]
    ok = any(find_label(nodes, w) for w in wanted)
    runlog.event("layout_check", target=step, result="ok" if ok else "mismatch",
                 detail={"expected": list(wanted), "seen": seen[:20]})
    if not ok:
        raise LayoutChanged(wanted, step, seen[:20])
    return nodes
