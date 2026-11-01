"""Per-run record: every action, every profile's outcome, every failure.

Each run gets `debug/runs/<YYYYmmdd-HHMMSS>/` (gitignored, kept forever):

  events.jsonl   one line per action (tap/swipe/key/screenshot/detect/...)
  run.log        Python logging: DEBUG to file, INFO to console
  summary.json   outcome counts, flagged cases, totals, config, git commit
  incidents/NN-<outcome>/   before/after frames, on-screen labels, detector
                 explain reports, last 20 events, error + traceback

Module-level "current run": with no active run every call is a no-op, so
scripts and tests that never call start_run() behave exactly as before.

Folders contain other people's profiles — they stay under debug/.
"""

import collections
import json
import logging
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("hinge")

# Outcomes that count as a sent like, and outcomes that are not flagged.
LIKE_OUTCOMES = {"liked_confirmed", "like_sheet_handled"}
FLAGGED_OK = {"liked_confirmed", "skipped"}

_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_SECRET_WORDS = ("KEY", "TOKEN", "SECRET")

_dir: Path | None = None
_profile: int | None = None
_tail: collections.deque = collections.deque(maxlen=20)
_outcomes: list[dict] = []
_incidents = 0
_handlers: list[logging.Handler] = []


def redact(s: str) -> str:
    return _KEY_RE.sub("sk-ant-REDACTED", s)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def start_run(runs_dir: Path | None = None) -> Path:
    """Create this run's folder and route the "hinge" logger into it."""
    global _dir, _profile, _incidents
    end_run()
    runs_dir = runs_dir or config.RUNS_DIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = runs_dir / stamp
    n = 1
    while folder.exists():  # two runs in the same second (tests)
        n += 1
        folder = runs_dir / f"{stamp}-{n}"
    folder.mkdir(parents=True)
    _dir, _profile, _incidents = folder, None, 0
    _tail.clear()
    _outcomes.clear()

    redactor = _RedactFilter()
    fh = logging.FileHandler(folder / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    fh.addFilter(redactor)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    ch.addFilter(redactor)
    for h in (fh, ch):
        log.addHandler(h)
        _handlers.append(h)
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return folder


def end_run() -> None:
    """Detach log handlers and clear the current run."""
    global _dir
    for h in _handlers:
        log.removeHandler(h)
        h.close()
    _handlers.clear()
    _dir = None


def run_id() -> str | None:
    return _dir.name if _dir else None


def run_dir() -> Path | None:
    return _dir


def set_profile(n: int | None) -> None:
    global _profile
    _profile = n


def event(kind: str, target: str | None = None, xy=None, result=None,
          detail=None, step: str | None = None) -> None:
    if _dir is None:
        return
    rec = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "profile": _profile, "step": step, "kind": kind, "target": target,
        "xy": list(xy) if xy is not None else None,
        "result": list(result) if isinstance(result, tuple) else result,
        "detail": detail,
    }
    line = redact(json.dumps(rec, ensure_ascii=False, default=str))
    _tail.append(line)
    with (_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def incident(outcome: str, before: bytes | None, after: bytes | None,
             error: BaseException | None = None) -> Path | None:
    """Snapshot everything needed to diagnose a flagged case."""
    global _incidents
    if _dir is None:
        return None
    import ui
    import vision

    _incidents += 1
    folder = _dir / "incidents" / f"{_incidents:02d}-{outcome}"
    folder.mkdir(parents=True)
    if before:
        (folder / "before.png").write_bytes(before)
    if after:
        (folder / "after.png").write_bytes(after)
    try:
        nodes = [vars(n) for n in ui.ui_texts()]
        ui_data: dict = {"nodes": nodes}
    except Exception as e:
        ui_data = {"error": repr(e)}
    (folder / "ui.json").write_text(json.dumps(ui_data, indent=1))
    detect = {}
    if after:
        for name, fn in (("heart", vision.find_first_heart),
                         ("send_like", vision.find_send_like)):
            try:
                result, report = fn(after, explain=True)
                detect[name] = {"result": list(result) if result else None,
                                "candidates": report}
            except Exception as e:
                detect[name] = {"error": repr(e)}
    (folder / "detect.json").write_text(json.dumps(detect, indent=1))
    (folder / "events_tail.jsonl").write_text("\n".join(_tail) + "\n")
    err = ""
    if error is not None:
        err = "".join(traceback.format_exception(error))
    (folder / "error.txt").write_text(redact(err))
    event("incident", target=outcome, detail=str(folder.relative_to(_dir)))
    return folder


def record_outcome(profile: int, outcome: str, incident_dir: Path | None) -> None:
    _outcomes.append({"profile": profile, "outcome": outcome,
                      "incident": str(incident_dir) if incident_dir else None})
    event("outcome", target=outcome, detail={"incident": _outcomes[-1]["incident"]})


def _flagged() -> list[dict]:
    return [o for o in _outcomes if o["outcome"] not in FLAGGED_OK]


def config_snapshot() -> dict:
    snap = {}
    for name in dir(config):
        if not name.isupper() or name == "PREFERENCES":
            continue
        if any(w in name for w in _SECRET_WORDS):
            continue
        value = getattr(config, name)
        try:
            json.dumps(value)
        except TypeError:
            continue
        snap[name] = value
    return snap


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=config.BASE_DIR, capture_output=True, text=True)
        return out.stdout.strip() or None
    except OSError:
        return None


def write_summary(exit_reason: str, totals: dict) -> Path | None:
    if _dir is None:
        return None
    counts: dict[str, int] = {}
    for o in _outcomes:
        counts[o["outcome"]] = counts.get(o["outcome"], 0) + 1
    data = {
        "run_id": run_id(),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "exit_reason": exit_reason,
        "outcomes": counts,
        "flagged": _flagged(),
        "totals": totals,
        "device": config.DEVICE,
        "serial": config.ADB_SERIAL,
        "git_commit": _git_commit(),
        "config": config_snapshot(),
    }
    path = _dir / "summary.json"
    path.write_text(redact(json.dumps(data, indent=1, default=str)))
    return path


def flagged_report() -> str:
    flagged = _flagged()
    if not flagged:
        return ""
    lines = [f"FLAGGED ({len(flagged)}):"]
    for f in flagged:
        lines.append(f"  profile {f['profile']:<3} {f['outcome']:<22} {f['incident'] or '-'}")
    return "\n".join(lines)
