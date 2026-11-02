"""runlog: per-run folder, events, incidents, summary."""

import json
import logging
import subprocess

import pytest

import adb
import config
import runlog
import ui
from tests.test_vision import _new_ui_frame, _png


@pytest.fixture
def run(tmp_path):
    folder = runlog.start_run(tmp_path)
    yield folder
    runlog.end_run()


def _events(folder):
    return [json.loads(l) for l in (folder / "events.jsonl").read_text().splitlines()]


def test_start_run_creates_folder_and_log(run):
    logging.getLogger("hinge").debug("hello debug")

    assert run.is_dir() and runlog.run_id() == run.name
    assert "hello debug" in (run / "run.log").read_text()


def test_event_appends_jsonl_with_profile(run):
    runlog.set_profile(3)
    runlog.event("detect", target="heart", result=[917, 1388])

    (e,) = _events(run)
    assert e["profile"] == 3 and e["kind"] == "detect" and e["result"] == [917, 1388]
    assert "ts" in e


def test_adb_tap_emits_event_with_target(run, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, b""))

    adb.tap(1, 2, target="skip_button")

    (e,) = _events(run)
    assert (e["kind"], e["target"], e["xy"]) == ("tap", "skip_button", [1, 2])


def test_no_active_run_events_are_noop(tmp_path):
    runlog.event("tap", target="x")  # must not raise

    assert runlog.run_id() is None


def test_incident_writes_bundle(run, monkeypatch):
    def boom():
        raise ui.UiDumpFailed("no dump")

    monkeypatch.setattr(ui, "ui_texts", boom)
    runlog.event("tap", target="send_like", xy=[708, 1168])
    frame = _png(_new_ui_frame((917, 1387)))

    folder = runlog.incident("like_unconfirmed", frame, frame, RuntimeError("why"))

    assert folder.name == "01-like_unconfirmed"
    for name in ("before.png", "after.png", "ui.json", "detect.json",
                 "events_tail.jsonl", "error.txt"):
        assert (folder / name).exists(), name
    assert "no dump" in (folder / "ui.json").read_text()
    detect = json.loads((folder / "detect.json").read_text())
    assert detect["heart"]["result"] == [917, 1387] or detect["heart"]["result"] is not None


def test_summary_counts_and_flags(run):
    runlog.record_outcome(1, "liked_confirmed", None)
    runlog.record_outcome(2, "skipped", None)
    runlog.record_outcome(3, "like_unconfirmed", run / "incidents/01-like_unconfirmed")

    data = json.loads(runlog.write_summary("halt: like_unconfirmed", {"cost": 0.1}).read_text())

    assert data["outcomes"] == {"liked_confirmed": 1, "skipped": 1, "like_unconfirmed": 1}
    assert [f["profile"] for f in data["flagged"]] == [3]
    assert data["exit_reason"] == "halt: like_unconfirmed"
    assert data["totals"] == {"cost": 0.1}


def test_config_snapshot_drops_secrets(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
    monkeypatch.setattr(config, "SOME_TOKEN", "t", raising=False)

    snap = runlog.config_snapshot()

    assert "ANTHROPIC_API_KEY" not in snap and "SOME_TOKEN" not in snap
    assert "PREFERENCES" not in snap
    assert snap["DRY_RUN"] == config.DRY_RUN


def test_redact_masks_anthropic_keys():
    assert runlog.redact("key=sk-ant-abc_DEF-123 ok") == "key=sk-ant-REDACTED ok"


def test_log_file_is_redacted(run):
    logging.getLogger("hinge").info("oops sk-ant-secret123")

    assert "secret123" not in (run / "run.log").read_text()


def test_flagged_report_format(run):
    runlog.record_outcome(4, "like_unconfirmed", run / "incidents/01-like_unconfirmed")

    out = runlog.flagged_report()

    assert "FLAGGED (1):" in out and "profile 4" in out and "01-like_unconfirmed" in out


def test_flagged_report_empty_when_clean(run):
    runlog.record_outcome(1, "skipped", None)

    assert runlog.flagged_report() == ""


def _fake_run(root, name, reason):
    d = root / name
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({
        "run_id": name, "exit_reason": reason, "outcomes": {"skipped": 2},
        "flagged": [{"profile": 2, "outcome": "skip_unconfirmed",
                     "incident": "incidents/01-skip_unconfirmed"}],
        "totals": {"likes_sent": 0},
    }))


def test_cli_last_prints_latest_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    _fake_run(tmp_path, "20261101-100000", "done")
    _fake_run(tmp_path, "20261101-120000", "halt: x")

    assert runlog.main(["last"]) == 0

    out = capsys.readouterr().out
    assert "20261101-120000" in out and "halt: x" in out
    assert "FLAGGED (1):" in out and "skip_unconfirmed" in out


def test_cli_by_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    _fake_run(tmp_path, "20261101-100000", "done")
    _fake_run(tmp_path, "20261101-120000", "halt: x")

    assert runlog.main(["20261101-100000"]) == 0
    assert "exit: done" in capsys.readouterr().out


def test_cli_unknown_run_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)

    assert runlog.main(["nope"]) == 1
    assert "no run nope under" in capsys.readouterr().out


def test_save_frame_writes_under_frames(run):
    runlog.set_profile(7)

    path = runlog.save_frame("post_send", b"png")

    assert path == run / "frames" / "07-post_send.png" and path.read_bytes() == b"png"


def test_save_frame_without_run_is_noop():
    assert runlog.save_frame("x", b"png") is None


def test_cli_prints_target_counts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    _fake_run(tmp_path, "20261101-100000", "done")
    lines = [{"kind": "target", "target": "identity", "result": "ok"},
             {"kind": "target", "target": "identity", "result": "ok"},
             {"kind": "target", "target": "prompt", "result": "prompt_matched"},
             {"kind": "target", "target": "prompt", "result": "fallback_photo1"},
             {"kind": "target", "target": "identity", "result": "name_mismatch"},
             {"kind": "tap", "target": "heart"}]
    (tmp_path / "20261101-100000" / "events.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + "\nnot json\n")

    assert runlog.main(["last"]) == 0

    out = capsys.readouterr().out
    assert ("target:   fallback_photo1=1  identity_mismatch=1  identity_ok=2  "
            "prompt_matched=1") in out


def test_cli_no_target_line_without_target_events(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    _fake_run(tmp_path, "20261101-100000", "done")

    assert runlog.main(["last"]) == 0
    assert "target:" not in capsys.readouterr().out
