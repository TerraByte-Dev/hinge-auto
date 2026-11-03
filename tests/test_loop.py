"""main.main(): per-profile outcomes, halt rules, summary, flagged report."""

import json
import logging
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

    def __init__(self, frames, decisions, likes=(), skips=(), skip_label="Skip p"):
        self.skip_label = skip_label
        self.frames = list(frames)
        self.decisions = list(decisions)
        self.likes = list(likes)
        self.skips = list(skips)
        self.like_calls = 0
        self.skip_calls = 0

    def capture(self):
        f = self.frames.pop(0) if self.frames else b"end"
        return [f], SimpleNamespace(skip_label=self.skip_label, frame=f)

    def judge(self, frames):
        d = self.decisions.pop(0)
        if isinstance(d, BaseException):
            raise d
        return d

    def do_like(self, message, ident, anchor=None):
        self.like_calls += 1
        # ident recorded as ("ident", frame) so assertions stay readable
        self.like_args = getattr(self, "like_args", []) + [(message, ("ident", ident.frame), anchor)]
        r = self.likes.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def do_skip(self):
        self.skip_calls += 1
        return self.skips.pop(0) if self.skips else "skipped"


@pytest.fixture
def loop(monkeypatch, tmp_path):
    def run(script, max_profiles=10, finalize=lambda d: "chosen"):
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
        monkeypatch.setattr(main, "finalize_opener", finalize)
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


def test_unsafe_recovery_halts(monkeypatch, tmp_path):
    s = Script([b"a"], [_decision()], likes=[main.LikeAborted("like_aborted_text", "x")])
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path)
    monkeypatch.setattr(config, "SAVE_DEBUG_FRAMES", False)
    monkeypatch.setattr(config, "MAX_PROFILES_PER_SESSION", 3)
    monkeypatch.setattr(adb, "check_device", lambda: "SERIAL")
    monkeypatch.setattr(adb, "screenshot", lambda: b"screen")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "_profile_region_hash", lambda png: png.decode())
    monkeypatch.setattr(main, "capture_profile", s.capture)
    monkeypatch.setattr(main, "judge", s.judge)
    monkeypatch.setattr(main, "finalize_opener", lambda d: "chosen")
    monkeypatch.setattr(main, "do_like", s.do_like)
    monkeypatch.setattr(main, "_run_preflight", lambda: None)
    monkeypatch.setattr(ui, "ui_texts", lambda: [])

    def unsafe():
        raise ui.UnsafeTap("no skip label at skip point")

    monkeypatch.setattr(main, "recover_from_failed_like", unsafe)
    try:
        code = main.main()
    finally:
        runlog.end_run()
    (run_dir,) = (tmp_path / "runs").iterdir()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert code == 1 and "unsafe_tap" in summary["exit_reason"]


# ---------- Task 5: identity + finalize in the loop ----------

def test_identity_halt_propagates_without_skip(loop):
    s = Script([b"a"], [_decision()],
               likes=[main.Halt("identity_mismatch: name_mismatch")])

    code, summary, run_dir = loop(s)

    assert code == 1 and summary["exit_reason"] == "halt: identity_mismatch: name_mismatch"
    assert s.skip_calls == 0
    assert summary["outcomes"] == {"identity_mismatch": 1}
    assert (run_dir / "incidents" / "01-identity_mismatch").is_dir()


def test_identity_from_capture_passed_to_do_like(loop):
    s = Script([b"a"], [_decision()], likes=["liked_confirmed"])

    loop(s, max_profiles=1)

    assert s.like_args == [("hi there", ("ident", b"a"), None)]


def test_finalize_runs_before_logging_and_do_like(loop, caplog):
    def fin(d):
        d.message = "the finalized one"
        d.anchor = {"id": "f2"}
        return "rewritten"

    s = Script([b"a"], [_decision()], likes=["liked_confirmed"])
    with caplog.at_level(logging.INFO, logger="hinge"):
        loop(s, max_profiles=1, finalize=fin)

    assert s.like_args == [("the finalized one", ("ident", b"a"), {"id": "f2"})]
    assert "Message:  the finalized one" in caplog.text
    assert "Opener:   rewritten" in caplog.text
    assert "hi there" not in caplog.text


def test_finalize_exception_means_no_comment(loop):
    def boom(d):
        raise ValueError("bad")

    s = Script([b"a"], [_decision()], likes=["liked_confirmed"])

    code, _, _ = loop(s, max_profiles=1, finalize=boom)

    assert code == 0 and s.like_args == [("", ("ident", b"a"), None)]


def test_history_reset_at_loop_start(loop):
    main._history.add("left over from an earlier run here")
    s = Script([b"a"], [_decision("skip")])

    loop(s, max_profiles=1)

    assert not main._history.starts_like("left over from an earlier run here")


def test_finalize_opener_uses_backend_complete_json(monkeypatch):
    calls = []
    backend = SimpleNamespace(complete_json="CJ")
    monkeypatch.setattr(main, "_backend", backend)
    monkeypatch.setattr(main.opener, "finalize",
                        lambda d, cj, h: calls.append((d, cj, h)) or "chosen")

    assert main.finalize_opener("D") == "chosen"
    assert calls == [("D", "CJ", main._history)]
    monkeypatch.setattr(main, "_backend", SimpleNamespace())
    main.finalize_opener("D")
    assert calls[-1][1] is None


def test_save_debug_records_opener_status(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVE_DEBUG_FRAMES", True)
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path)

    main.save_debug([b"png"], _decision(), 3, "chosen")

    text = (tmp_path / "liked" / "03_p" / "decision.txt").read_text()
    assert "opener_status: chosen" in text and "message: hi there" in text


# ---------- R23: no identity at capture -> no like attempt ----------

def test_no_identity_on_phone_skips_instead_of_liking(loop, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    s = Script([b"a"], [_decision()], likes=["liked_confirmed"], skip_label=None)

    code, summary, run_dir = loop(s, max_profiles=1)

    assert code == 0 and s.like_calls == 0 and s.skip_calls == 1
    assert summary["totals"]["likes_sent"] == 0 and summary["totals"]["skips"] == 1
    assert summary["outcomes"] == {"no_identity": 1, "skipped": 1}
    assert (run_dir / "incidents" / "01-no_identity").is_dir()


def test_no_identity_skip_decision_unchanged(loop, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")
    s = Script([b"a"], [_decision("skip")], skip_label=None)

    _, summary, _ = loop(s, max_profiles=1)

    assert s.skip_calls == 1 and summary["outcomes"] == {"skipped": 1}


def test_no_identity_on_emulator_still_likes(loop, monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    s = Script([b"a"], [_decision()], likes=["liked_confirmed"], skip_label=None)

    _, summary, _ = loop(s, max_profiles=1)

    assert s.like_calls == 1 and summary["outcomes"] == {"liked_confirmed": 1}


# ---------- openerlog: one record per like, across runs ----------

def test_like_writes_opener_record_with_outcome(loop, tmp_path):
    s = Script([b"a", b"b"], [_decision(), _decision("skip")], likes=["liked_confirmed"])

    _, summary, _ = loop(s, max_profiles=2)

    (line,) = (tmp_path / "openers.jsonl").read_text().splitlines()
    rec = json.loads(line)
    assert rec["run_id"] == summary["run_id"] and rec["profile"] == 1
    assert rec["outcome"] == "liked_confirmed" and rec["status"] == "chosen"
    assert rec["final"] == "hi there" and rec["opener_sent"] is True


def test_dry_run_like_record_not_sent(loop, tmp_path):
    s = Script([b"a"], [_decision()], likes=["skipped"])

    loop(s, max_profiles=1)

    assert json.loads((tmp_path / "openers.jsonl").read_text())["opener_sent"] is False


def test_openerlog_failure_does_not_stop_loop(loop, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(main.openerlog, "record", boom)
    s = Script([b"a"], [_decision()], likes=["liked_confirmed"])

    code, summary, _ = loop(s, max_profiles=1)

    assert code == 0 and summary["totals"]["likes_sent"] == 1


def test_save_debug_is_per_run_and_returns_imgs(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVE_DEBUG_FRAMES", True)
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path)
    monkeypatch.setattr(runlog, "run_id", lambda: "20261103-120000")

    imgs = main.save_debug([b"png"], _decision(), 3, "chosen")

    assert imgs == tmp_path / "liked" / "20261103-120000" / "03_p" / "imgs"
    assert (imgs / "frame_00.png").read_bytes() == b"png"


def test_like_rewrites_live_html(loop, tmp_path):
    s = Script([b"a"], [_decision()], likes=["liked_confirmed"])

    loop(s, max_profiles=1)

    assert "hi there" in (tmp_path / "openers.html").read_text()


def test_html_failure_does_not_stop_loop(loop, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(main.openerlog, "write_html", boom)
    s = Script([b"a"], [_decision()], likes=["liked_confirmed"])

    code, summary, _ = loop(s, max_profiles=1)

    assert code == 0 and summary["totals"]["likes_sent"] == 1


def test_halted_like_still_gets_opener_record(loop, tmp_path):
    s = Script([b"a"], [_decision()], likes=[main.Halt("identity_mismatch: no_label")])

    code, _, _ = loop(s, max_profiles=1)

    assert code == 1
    rec = json.loads((tmp_path / "openers.jsonl").read_text())
    assert rec["outcome"] == "identity_mismatch" and rec["opener_sent"] is False
    assert rec["final"] == "hi there"
