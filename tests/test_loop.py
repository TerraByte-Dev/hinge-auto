"""main.main(): per-profile outcomes, halt rules, summary, flagged report."""

import json
import sys
from types import SimpleNamespace

import pytest

import adb
import config
import main
import metrics
import runlog
import ui


def _decision(kind="like", name="p"):
    return SimpleNamespace(
        name=name, decision=kind, confidence="high", reasoning="r",
        message="hi there" if kind == "like" else "", message_archetype="x",
        skip_reason="" if kind == "like" else "nope", premade_id=None,
        prompt_referenced=None, usage={"input_tokens": 1000, "output_tokens": 100},
    )


class Script:
    """Feeds the loop: one frame id per profile, scripted judge decisions
    and scripted do_like results (a return value or an exception)."""

    def __init__(self, frames, decisions, likes=(), skips=()):
        self.frames = list(frames)
        self.decisions = list(decisions)
        self.likes = list(likes)
        self.skips = list(skips)
        self.like_calls = 0
        self.skip_calls = 0

    def capture(self):
        f = self.frames.pop(0) if self.frames else b"end"
        return [f]

    def judge(self, frames):
        d = self.decisions.pop(0)
        if isinstance(d, BaseException):
            raise d
        return d

    def do_like(self, message=""):
        self.like_calls += 1
        r = self.likes.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def do_skip(self):
        self.skip_calls += 1
        return self.skips.pop(0) if self.skips else "skipped"


@pytest.fixture
def loop(monkeypatch, tmp_path):
    def run(script, max_profiles=10):
        monkeypatch.setattr(sys, "argv", ["main.py"])
        monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(config, "DEBUG_DIR", tmp_path)
        monkeypatch.setattr(config, "SAVE_DEBUG_FRAMES", False)
        monkeypatch.setattr(config, "MAX_PROFILES_PER_SESSION", max_profiles)
        monkeypatch.setattr(config, "MAX_LIKES_PER_SESSION", 8)
        monkeypatch.setattr(adb, "check_device", lambda: "SERIAL")
        monkeypatch.setattr(adb, "screenshot", lambda: b"screen")
        monkeypatch.setattr(main.time, "sleep", lambda s: None)
        monkeypatch.setattr(main, "_profile_region_hash", lambda png: png.decode())
        monkeypatch.setattr(main, "capture_profile", script.capture)
        monkeypatch.setattr(main, "judge", script.judge)
        monkeypatch.setattr(main, "do_like", script.do_like)
        monkeypatch.setattr(main, "do_skip", script.do_skip)
        monkeypatch.setattr(main, "recover_from_failed_like", lambda: script.do_skip())
        monkeypatch.setattr(ui, "ui_texts", lambda: [])
        monkeypatch.setattr(main, "_run_preflight", lambda: None)
        code = main.main()
        (run_dir,) = (tmp_path / "runs").iterdir()
        summary = json.loads((run_dir / "summary.json").read_text())
        return code, summary, run_dir

    yield run
    runlog.end_run()


def test_like_counted_only_when_confirmed(loop):
    s = Script([b"a", b"b"], [_decision(), _decision()],
               likes=["like_sheet_handled", main.LikeUnconfirmed("no advance")])

    code, summary, run_dir = loop(s)

    assert code == 1
    assert summary["exit_reason"].startswith("halt")
    assert summary["totals"]["likes_sent"] == 1
    assert summary["outcomes"] == {"like_sheet_handled": 1, "like_unconfirmed": 1}
    assert (run_dir / "incidents" / "02-like_unconfirmed").is_dir()


def test_halt_does_not_skip_after_unconfirmed_like(loop):
    s = Script([b"a"], [_decision()], likes=[main.LikeUnconfirmed("?")])

    loop(s)

    assert s.skip_calls == 0


def test_two_detection_aborts_in_a_row_halt(loop):
    abort = lambda: main.LikeAborted("like_aborted_detection", "no heart")
    s = Script([b"a", b"b", b"c"], [_decision()] * 3, likes=[abort(), abort(), "liked_confirmed"])

    code, summary, _ = loop(s)

    assert code == 1 and s.like_calls == 2
    assert "like_aborted_detection" in summary["exit_reason"]


def test_detection_abort_then_success_does_not_halt(loop):
    s = Script([b"a", b"b", b"c"], [_decision()] * 3,
               likes=[main.LikeAborted("like_aborted_detection", "x"),
                      "liked_confirmed",
                      main.LikeAborted("like_aborted_detection", "x")])

    code, summary, _ = loop(s, max_profiles=3)

    assert code == 0 and summary["totals"]["likes_sent"] == 1


def test_three_duplicates_halt(loop):
    s = Script([b"a", b"a", b"a", b"a", b"b"], [_decision("skip")])

    code, summary, _ = loop(s)

    assert code == 1 and summary["outcomes"]["duplicate"] == 3
    assert "duplicate" in summary["exit_reason"]


def test_unsafe_tap_halts(loop):
    s = Script([b"a"], [_decision()], likes=[ui.UnsafeTap("rose")])

    code, summary, _ = loop(s)

    assert code == 1 and summary["outcomes"] == {"unsafe_tap": 1}


def test_unexpected_like_error_halts_as_unconfirmed(loop):
    s = Script([b"a"], [_decision()], likes=[OSError("adb gone")])

    code, summary, _ = loop(s)

    assert code == 1 and summary["outcomes"] == {"like_unconfirmed": 1}


def test_summary_written_on_keyboard_interrupt(loop):
    s = Script([b"a"], [KeyboardInterrupt()])

    code, summary, _ = loop(s)

    assert summary["exit_reason"] == "interrupted" and code == 130


def test_skip_unconfirmed_is_flagged_with_incident(loop):
    s = Script([b"a"], [_decision("skip")], skips=["skip_unconfirmed"])

    _, summary, run_dir = loop(s, max_profiles=1)

    assert summary["flagged"][0]["outcome"] == "skip_unconfirmed"
    assert (run_dir / "incidents" / "01-skip_unconfirmed").is_dir()


def test_session_log_has_outcome_and_run_id(loop, tmp_path):
    s = Script([b"a"], [_decision("skip")])

    _, summary, _ = loop(s, max_profiles=1)

    rec = json.loads((tmp_path / "session_log.jsonl").read_text().splitlines()[-1])
    assert rec["outcome"] == "skipped" and rec["run_id"] == summary["run_id"]


def test_flagged_report_printed(loop, capsys):
    s = Script([b"a"], [_decision("skip")], skips=["skip_unconfirmed"])

    loop(s, max_profiles=1)

    assert "FLAGGED (1):" in capsys.readouterr().out


def test_dry_run_like_counts_as_skip(loop):
    s = Script([b"a"], [_decision()], likes=["skipped"])

    _, summary, _ = loop(s, max_profiles=1)

    assert summary["totals"]["likes_sent"] == 0 and summary["outcomes"] == {"skipped": 1}
