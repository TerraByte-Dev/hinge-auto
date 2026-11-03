"""openerlog: per-like opener record (debug/openers.jsonl) and its viewers."""
import json

import pytest

import config
import openerlog
from judge_common import Decision

FACTS = [{"id": "f1", "kind": "prompt", "index": 1, "prompt_text": "Typical Sunday",
          "answer_text": "nap either way", "detail": "sunday"},
         {"id": "f2", "kind": "photo", "index": 2, "detail": "sourdough loaf"}]


def _decision(final="nap either way is a solid plan", status="chosen", **kw):
    d = Decision(name="Ana", decision="like", confidence="high", reasoning="r",
                 message=final, profile_facts=[dict(f) for f in FACTS])
    d.opener_trace = {
        "candidates": [
            {"text": "nap either way is a solid plan", "fact_id": "f1",
             "form": "observation", "stage1_pick": False, "reject": []},
            {"text": "genuinely love that loaf", "fact_id": "f2",
             "form": "observation", "stage1_pick": True, "reject": ["ai_tell"]},
        ],
        "stage2": [{"choice_index": 0, "reason": "most specific", "rewrite": None}],
        "status": status, "final": final,
    }
    for k, v in kw.items():
        setattr(d, k, v)
    return d


@pytest.fixture
def log_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEBUG_DIR", tmp_path)
    monkeypatch.setattr(config, "MODE_NAME", "m1")
    monkeypatch.setattr(openerlog, "_prompt_hash", lambda: "abc123")
    monkeypatch.setattr(openerlog, "_git_commit", lambda: "deadbee")
    return tmp_path / "openers.jsonl"


def _rec(**kw):
    args = {"run_id": "r1", "profile": 3, "decision": _decision(),
            "status": "chosen", "outcome": "liked_confirmed", "frames_dir": None}
    args.update(kw)
    openerlog.record(**args)


def test_record_appends_one_line_per_like(log_path):
    _rec()
    _rec(profile=4)

    recs = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert [r["profile"] for r in recs] == [3, 4]
    r = recs[0]
    assert r["schema"] == openerlog.SCHEMA == 1
    assert (r["run_id"], r["name"], r["mode"]) == ("r1", "Ana", "m1")
    assert (r["prompt_hash"], r["git_commit"]) == ("abc123", "deadbee")
    assert r["profile_facts"] == FACTS
    assert r["candidates"][1]["reject"] == ["ai_tell"]
    assert r["stage2"][0]["reason"] == "most specific"
    assert r["final"] == "nap either way is a solid plan"
    assert r["opener_sent"] is True and r["dry_run"] == config.DRY_RUN


@pytest.mark.parametrize("outcome,final,sent", [
    ("liked_confirmed", "hi there friend", True),
    ("like_sheet_handled", "hi there friend", True),
    ("liked_confirmed", "", False),          # like went out without a comment
    ("like_aborted_text", "hi there friend", False),
    ("skipped", "hi there friend", False),   # DRY_RUN
])
def test_opener_sent_only_for_confirmed_like_with_text(log_path, outcome, final, sent):
    _rec(decision=_decision(final=final), outcome=outcome)
    assert json.loads(log_path.read_text())["opener_sent"] is sent


def test_skip_decision_writes_nothing(log_path):
    _rec(decision=_decision(decision="skip"), outcome="skipped")
    assert not log_path.exists()


def test_finalize_error_still_recorded(log_path):
    d = _decision(final="")
    d.opener_trace = {}
    _rec(decision=d, status="finalize_error")
    r = json.loads(log_path.read_text())
    assert r["status"] == "finalize_error" and r["candidates"] == [] and r["final"] == ""


def test_api_keys_redacted(log_path):
    _rec(decision=_decision(final="sk-ant-abc123XYZ oops"))
    assert "sk-ant-abc123XYZ" not in log_path.read_text()


def test_frames_dir_stored_relative_to_debug(log_path, tmp_path):
    _rec(frames_dir=tmp_path / "liked" / "r1" / "03_ana" / "imgs")
    assert json.loads(log_path.read_text())["frames_dir"] == "liked/r1/03_ana/imgs"


def test_load_skips_bad_lines_and_tolerates_missing_fields(log_path):
    _rec()
    with log_path.open("a") as f:
        f.write('{"schema": 1, "run_id": "r0", "profile": 1}\n')  # old/partial
        f.write('{"truncated": \n')                                 # crash mid-write
    recs = openerlog.load(log_path)
    assert [r["run_id"] for r in recs] == ["r1", "r0"]


def test_select_last_run_all_and_sent_only(log_path):
    _rec(run_id="r1")
    _rec(run_id="r2", outcome="skipped")
    _rec(run_id="r2", profile=4)
    recs = openerlog.load(log_path)

    assert [r["profile"] for r in openerlog.select(recs, "last")] == [3, 4]
    assert len(openerlog.select(recs, "all")) == 3
    assert len(openerlog.select(recs, "r1")) == 1
    assert [r["run_id"] for r in openerlog.select(recs, "all", sent_only=True)] == ["r1", "r2"]


def test_render_text_marks_rejects_pick_and_final(log_path):
    _rec()
    out = openerlog.render_text(openerlog.load(log_path))
    assert "Ana" in out and "most specific" in out
    assert "ai_tell" in out and "sourdough loaf" in out
    lines = out.splitlines()
    sent_line = next(l for l in lines if "nap either way is a solid plan" in l and "[" in l)
    assert "★" in sent_line
    picked = next(l for l in lines if "genuinely love that loaf" in l)
    assert "✗" in picked and "▸" in picked


def test_render_text_handles_partial_old_records():
    out = openerlog.render_text([{"schema": 1, "run_id": "r0", "profile": 1}])
    assert "r0" in out


def test_summary_counts(log_path):
    _rec()                                                      # sent, overrode pick
    _rec(decision=_decision(final="", status="empty_all_rejected"),
         status="empty_all_rejected")
    s = openerlog.summary(openerlog.load(log_path))
    assert "2 likes" in s and "1 opener sent" in s
    assert "empty_all_rejected=1" in s
    assert "stage 2 overrode stage 1's pick: 1/1" in s
    assert "ai_tell=2" in s


def test_render_html_escapes_and_links_frames(log_path, tmp_path):
    imgs = tmp_path / "liked" / "r1" / "03_ana" / "imgs"
    imgs.mkdir(parents=True)
    (imgs / "frame_00.png").write_bytes(b"png")
    (imgs / "frame_01.png").write_bytes(b"png")
    _rec(decision=_decision(final="<script>x</script> is fine"), frames_dir=imgs)

    html = openerlog.render_html(openerlog.load(log_path), tmp_path)
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert 'src="liked/r1/03_ana/imgs/frame_00.png"' in html
    assert 'src="liked/r1/03_ana/imgs/frame_01.png"' in html


def test_main_prints_and_writes_html(log_path, tmp_path, capsys):
    _rec()
    assert openerlog.main(["all"]) == 0
    assert "Ana" in capsys.readouterr().out
    assert openerlog.main(["all", "--html"]) == 0
    assert (tmp_path / "openers.html").exists()


def test_main_no_records(log_path, capsys):
    assert openerlog.main([]) == 1
    assert "no opener records" in capsys.readouterr().out


# ---------- live HTML: rewritten by the loop, reloads itself ----------

def test_render_html_refresh_tag_only_when_asked(log_path):
    _rec()
    recs = openerlog.load(log_path)
    assert 'http-equiv="refresh"' not in openerlog.render_html(recs, config.DEBUG_DIR)
    html = openerlog.render_html(recs, config.DEBUG_DIR, refresh=30)
    assert '<meta http-equiv="refresh" content="30">' in html
    assert "scrollY" in html  # keeps the reader's place across reloads


def test_write_html_covers_all_runs_and_leaves_no_temp(log_path, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OPENERLOG_HTML_REFRESH", 15)
    _rec(run_id="r1")
    _rec(run_id="r2")

    out = openerlog.write_html()

    assert out == tmp_path / "openers.html"
    text = out.read_text()
    assert "run r1" in text and "run r2" in text
    assert 'content="15"' in text
    assert sorted(p.name for p in tmp_path.iterdir()) == ["openers.html", "openers.jsonl"]


def test_write_html_refresh_off_when_zero(log_path, monkeypatch):
    monkeypatch.setattr(config, "OPENERLOG_HTML_REFRESH", 0)
    _rec()
    assert 'http-equiv="refresh"' not in openerlog.write_html().read_text()


def test_main_watch_writes_refreshing_html(log_path, tmp_path):
    _rec()
    assert openerlog.main(["all", "--watch", "10"]) == 0
    assert 'content="10"' in (tmp_path / "openers.html").read_text()
