"""Eval report metrics/acceptance and the run.py loop (backend faked)."""
import json

import pytest

import config
from evals.openers import report, run

PROFILES = {
    "p1": {"id": "p1", "name": "ana", "tags": ["playful"], "info": "Austin",
           "elements": [{"kind": "photo", "caption": "woman with a kayak on a lake"},
                        {"kind": "prompt", "prompt": "My simple pleasures",
                         "answer": "Sourdough on Sunday mornings"}],
           "expected_absent": ["dog", "hiking"]},
    "p2": {"id": "p2", "name": "bo", "tags": ["serious", "mixed"], "info": "Denver",
           "elements": [{"kind": "photo", "caption": "man at a chess board"}],
           "expected_absent": ["beach"]},
    "p3": {"id": "p3", "name": "cy", "tags": ["sparse"], "info": "Ohio",
           "elements": [{"kind": "photo", "caption": "woman in a green jacket"}],
           "expected_absent": []},
}


def row(pid, message="", status=None, pipeline="new", decision="like",
        anchor_kind="", form="question", error=None):
    if status is None:
        status = "chosen" if message else "empty_no_candidates"
    return {"profile_id": pid, "tags": PROFILES[pid]["tags"], "pipeline": pipeline,
            "backend": "fake", "decision": {"decision": decision},
            "message": message, "form": form if message else "empty",
            "anchor_kind": anchor_kind, "anchor_label": "", "status": status,
            "n_facts": 1, "n_candidates": 1, "usage": {}, "seconds": 1.0,
            "error": error}


def test_coverage_overall_and_per_tag():
    rows = [row("p1", "Which lake was the kayak photo taken on?", anchor_kind="photo"),
            row("p2", ""), row("p3", "Where did you find that green jacket?")]
    s = report.summarise(rows, PROFILES)
    assert s["likes"] == 3
    assert s["coverage"] == pytest.approx(2 / 3)
    assert s["coverage_by_tag"]["playful"] == 1.0
    assert s["coverage_by_tag"]["serious"] == 0.0
    assert s["coverage_by_tag"]["sparse"] == 1.0
    assert s["empty_reasons"] == {"empty_no_candidates": 1}


def test_skips_and_errors_are_not_likes_but_are_counted():
    rows = [row("p1", "Which lake was the kayak photo taken on?"),
            row("p2", status="skip", decision="skip"),
            row("p3", status="error", decision=None, error="boom")]
    s = report.summarise(rows, PROFILES)
    assert s["likes"] == 1 and s["skips"] == 1 and s["errors"] == 1
    assert s["coverage"] == 1.0
    assert s["coverage_all_rows"] == pytest.approx(1 / 3)


def test_quality_counts_nonempty_only():
    rows = [row("p1", "Which lake was the kayak photo taken on?"),
            row("p2", "")] + [row("p3", "")] * 3
    s = report.summarise(rows, PROFILES)
    assert s["nonempty"] == 1
    assert s["question_rate"] == 1.0
    assert s["median_length"] == len("Which lake was the kayak photo taken on?")


def test_question_rate_and_premade_rewrite_rates():
    rows = [row("p1", "Kayak on a lake looks like a calm Sunday.", status="rewritten",
                form="observation"),
            row("p2", "Chess board in the photo, are you any good?"),
            row("p3", "hey there friend, nice jacket", status="premade", form="premade")]
    s = report.summarise(rows, PROFILES)
    assert s["question_rate"] == pytest.approx(1 / 3)
    assert s["premade_rate"] == pytest.approx(1 / 3)
    assert s["rewrite_rate"] == pytest.approx(1 / 3)


def test_all_empty_run():
    rows = [row(p, "") for p in PROFILES]
    s = report.summarise(rows, PROFILES)
    assert s["coverage"] == 0.0
    assert s["question_rate"] is None
    assert s["acceptance"]["passed"] is False


def test_expected_absent_whole_word_case_insensitive():
    rows = [row("p1", "Is the Dog on the kayak trip too?"),
            row("p2", "Is the beaches near the chess club?"),  # not a whole-word hit
            row("p3", "Hiking jacket? nope")]  # 'hiking' not absent for p3
    s = report.summarise(rows, PROFILES)
    hits = s["expected_absent_hits"]
    assert [(h["profile_id"], h["term"]) for h in hits] == [("p1", "dog")]


def test_shared_experience_and_sender_bio_flagged():
    rows = [row("p1", "I spent a summer kayaking, which lake is that?"),
            row("p2", "Chess, me too, what opening do you play?"),
            row("p3", "Where did you find that green jacket?")]
    s = report.summarise(rows, PROFILES)
    assert sorted(f["profile_id"] for f in s["sender_bio_flags"]) == ["p1", "p2"]


def test_filter_violations_and_banned_hits():
    rows = [row("p1", "Honestly that kayak photo is iconic, which lake?"),
            row("p2", "Chess board in the photo, are you any good?")]
    s = report.summarise(rows, PROFILES)
    assert s["banned_hits"].get("ai_tell") == 1
    assert len(s["filter_violations"]) == 1
    assert s["filter_violations"][0]["profile_id"] == "p1"


def test_grounding_and_opening_repetition():
    rows = [row("p1", "Which lake was the kayak photo taken on?"),
            row("p2", "Which piece do you open with at chess?"),
            row("p3", "Totally random words about volcanoes here.")]
    s = report.summarise(rows, PROFILES)
    assert s["grounded_rate"] == pytest.approx(2 / 3)
    assert s["top_opening_word"] == ("which", pytest.approx(2 / 3))


def test_predicted_target():
    assert report.predicted_target({"anchor_kind": "prompt"}) == "prompt"
    for k in ("photo", "bio", "unknown", "", None):
        assert report.predicted_target({"anchor_kind": k}) == "photo1"
    rows = [row("p1", "Sourdough on Sunday, what is your starter called?",
                anchor_kind="prompt"),
            row("p2", "Chess board in the photo, are you any good?",
                anchor_kind="photo"),
            row("p3", "")]
    s = report.summarise(rows, PROFILES)
    assert s["target_mix"] == {"prompt": pytest.approx(1 / 3),
                               "photo1": pytest.approx(2 / 3)}
    assert s["anchor_kind_mix"] == {"prompt": 1, "photo": 1}


GOOD = {"p1": "Sourdough on Sunday, what is your starter called?",
        "p2": "Chess board in the photo, what opening do you play",
        "p3": "That green jacket is a strong colour choice"}


def test_acceptance_passes_against_legacy():
    legacy = report.summarise(
        [row(p, m + "?", pipeline="legacy") for p, m in GOOD.items()], PROFILES)
    new = report.summarise([row(p, m) for p, m in GOOD.items()], PROFILES, baseline=legacy)
    acc = new["acceptance"]
    assert acc["passed"] is True, acc
    assert {i["status"] for i in acc["items"]} == {"pass"}


def test_acceptance_fails_tag_drop_and_question_rate():
    legacy = report.summarise([row(p, m, pipeline="legacy") for p, m in GOOD.items()],
                              PROFILES)
    rows = [row("p1", GOOD["p1"]), row("p2", ""), row("p3", GOOD["p3"] + "?")]
    new = report.summarise(rows, PROFILES, baseline=legacy)
    by = {i["id"]: i["status"] for i in new["acceptance"]["items"]}
    assert by["tag_drop_vs_legacy"] == "fail"
    assert by["question_rate_below_legacy"] == "fail"
    assert by["coverage_non_sparse"] == "fail"  # 1/2 < 0.85
    assert by["coverage_sparse"] == "pass"
    assert new["acceptance"]["passed"] is False


def test_acceptance_without_legacy_is_na_and_not_passed():
    s = report.summarise([row(p, m) for p, m in GOOD.items()], PROFILES)
    by = {i["id"]: i["status"] for i in s["acceptance"]["items"]}
    assert by["tag_drop_vs_legacy"] == "n/a"
    assert by["question_rate_below_legacy"] == "n/a"
    assert by["coverage_non_sparse"] == "pass"
    assert s["acceptance"]["passed"] is False


def test_acceptance_hallucination_item():
    s = report.summarise([row("p1", "Is the dog coming kayaking too?")], PROFILES)
    by = {i["id"]: i["status"] for i in s["acceptance"]["items"]}
    assert by["zero_expected_absent"] == "fail"


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_report_main_writes_md_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "load_profiles", lambda: PROFILES)
    a, b = tmp_path / "new.jsonl", tmp_path / "legacy.jsonl"
    _write(a, [row(p, m) for p, m in GOOD.items()])
    _write(b, [row(p, m + "?", pipeline="legacy", anchor_kind="unknown")
               for p, m in GOOD.items()])
    assert report.main([str(a), str(b), "--out", str(tmp_path / "rep")]) == 0
    md = (tmp_path / "rep.md").read_text()
    html = (tmp_path / "rep.html").read_text()
    for text in (md, html):
        assert "heuristic" in text and "not match rates" in text
        assert "Acceptance" in text
        assert "Chess board in the photo" in text
    assert "<style>" in html and "http" not in html.split("<body>")[0].replace(
        "http-equiv", "")
    assert "<script" not in html


def test_report_html_escapes(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "load_profiles", lambda: PROFILES)
    a = tmp_path / "a.jsonl"
    _write(a, [row("p1", "<b>Sourdough</b> starter name?")])
    report.main([str(a), "--out", str(tmp_path / "r")])
    assert "<b>Sourdough" not in (tmp_path / "r.html").read_text()


# ---------------------------------------------------------------- run.py

class FakeBackend:
    def __init__(self, out=None, raise_for=(), stage2=None):
        self.out = out
        self.raise_for = set(raise_for)
        self.stage2 = stage2 or {"choice_index": 0, "rewrite": None, "reason": "ok"}
        self.vision_calls = []
        self.text_calls = []

    def vision_json(self, system, frames, schema, name):
        self.vision_calls.append((system, list(frames), schema, name))
        pid = frames[0].parent.name
        if pid in self.raise_for:
            raise RuntimeError(f"fail {pid}")
        return dict(self.out(pid), _usage={"input_tokens": 1})

    def complete_json(self, system, user, schema, name):
        self.text_calls.append(name)
        return dict(self.stage2, _usage={"input_tokens": 2})


EVAL_PROFILES = [
    {"id": "p1", "name": "ana", "age": 28, "tags": ["playful"], "info": "Austin",
     "elements": [{"kind": "photo", "caption": "woman with a kayak on a lake"},
                  {"kind": "prompt", "prompt": "My simple pleasures",
                   "answer": "Sourdough on Sunday mornings"}],
     "expected_absent": []},
    {"id": "p2", "name": "bo", "age": 31, "tags": ["serious"], "info": "Denver",
     "elements": [{"kind": "photo", "caption": "man at a chess board"}],
     "expected_absent": []},
]


def _new_out(pid):
    return {"name": pid, "decision": "like", "confidence": "high", "reasoning": "r",
            "message": "", "skip_reason": "none", "message_archetype": "question",
            "premade_id": "", "prompt_referenced": "",
            "profile_facts": [{"id": "f1", "kind": "prompt", "index": 1,
                               "prompt_text": "My simple pleasures",
                               "answer_text": "Sourdough on Sunday mornings"}],
            "candidates": [{"text": "Sourdough on Sunday, what is your starter called?",
                            "fact_id": "f1", "form": "question"}],
            "pick_index": 0}


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "load_profiles", lambda: EVAL_PROFILES)
    monkeypatch.setattr(run, "FRAMES_ROOT", tmp_path / "frames")
    return tmp_path


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_run_new_pipeline(run_env, monkeypatch, capsys):
    fake = FakeBackend(out=_new_out)
    monkeypatch.setattr(run, "make_backend", lambda name, model: fake)
    before = (config.PREFERENCES, config.PREMADES, config.MESSAGE_VOICE)
    out = run_env / "o.jsonl"
    seen = {}

    real = run.build_system_prompt

    def spy():
        seen["prefs"] = config.PREFERENCES
        seen["premades"] = config.PREMADES
        seen["age"] = (config.AGE_MIN, config.AGE_MAX)
        seen["voice"] = config.MESSAGE_VOICE
        return real()

    monkeypatch.setattr(run, "build_system_prompt", spy)
    assert run.main(["--backend", "claude-cli", "--pipeline", "new", "--out", str(out)]) == 0
    rows = _rows(out)
    assert [r["profile_id"] for r in rows] == ["p1", "p2"]
    r = rows[0]
    assert set(r) == {"profile_id", "tags", "pipeline", "backend", "model", "decision",
                      "message", "form", "anchor_kind", "anchor_label", "status",
                      "n_facts", "n_candidates", "usage", "seconds", "error"}
    assert r["status"] == "chosen"
    assert r["message"] == "Sourdough on Sunday, what is your starter called?"
    assert r["form"] == "question" and r["anchor_kind"] == "prompt"
    assert r["anchor_label"] == "My simple pleasures"
    assert r["pipeline"] == "new" and r["backend"] == "claude-cli"
    assert r["usage"]["input_tokens"] == 3
    assert r["error"] is None
    # same phrasing on p2 is rejected as a repeat by the per-run history
    assert rows[1]["status"] != "chosen"
    assert seen == {"prefs": "Decision: like every profile.", "premades": [],
                    "age": (None, None), "voice": None}
    assert (config.PREFERENCES, config.PREMADES, config.MESSAGE_VOICE) == before
    sys_text, frames, schema, name = fake.vision_calls[0]
    assert name == "submit_decision" and "profile_facts" in schema["properties"]
    assert frames and all(f.exists() for f in frames)
    printed = capsys.readouterr().out
    assert "p1" in printed and "chosen" in printed


def test_run_legacy_pipeline_and_errors(run_env, monkeypatch, capsys):
    from evals.openers import legacy_prompt

    def legacy_out(pid):
        return {"name": pid, "decision": "like", "confidence": "high", "reasoning": "r",
                "message": "hey, your [prompt] caught my eye, what's the context",
                "skip_reason": "none", "message_archetype": "premade",
                "premade_id": "prompt_curious", "prompt_referenced": "kayak photo"}

    fake = FakeBackend(out=legacy_out, raise_for={"p2"})
    monkeypatch.setattr(run, "make_backend", lambda name, model: fake)
    out = run_env / "l.jsonl"
    assert run.main(["--backend", "codex-cli", "--pipeline", "legacy",
                     "--out", str(out)]) == 0
    rows = _rows(out)
    assert rows[0]["message"] == "hey, your [prompt] caught my eye, what's the context"
    assert rows[0]["form"] == "premade" and rows[0]["status"] == "premade"
    assert rows[0]["anchor_kind"] == "unknown"
    assert rows[0]["anchor_label"] == "kayak photo"
    assert rows[0]["n_facts"] == 0
    assert rows[1]["status"] == "error" and "fail p2" in rows[1]["error"]
    assert fake.vision_calls[0][0] == legacy_prompt.SYSTEM_PROMPT
    assert fake.vision_calls[0][2] is legacy_prompt.SCHEMA
    assert fake.text_calls == []


def test_run_limit_and_tags(run_env, monkeypatch):
    fake = FakeBackend(out=_new_out)
    monkeypatch.setattr(run, "make_backend", lambda name, model: fake)
    out = run_env / "t.jsonl"
    run.main(["--backend", "claude-cli", "--pipeline", "new", "--tags", "serious",
              "--out", str(out)])
    assert [r["profile_id"] for r in _rows(out)] == ["p2"]
    run.main(["--backend", "claude-cli", "--pipeline", "new", "--limit", "1",
              "--out", str(out)])
    assert [r["profile_id"] for r in _rows(out)] == ["p1"]


def test_run_warns_for_paid_backend(run_env, monkeypatch, capsys):
    fake = FakeBackend(out=_new_out)
    monkeypatch.setattr(run, "make_backend", lambda name, model: fake)
    run.main(["--backend", "anthropic", "--pipeline", "new", "--limit", "1",
              "--out", str(run_env / "a.jsonl")])
    err = capsys.readouterr().err
    assert "API key" in err and "costs money" in err


def test_default_out_path():
    p = run.default_out("claude-cli", "new")
    assert p.parent.name == "results"
    assert p.name.endswith("-claude-cli-new.jsonl")


# ---------------------------------------------------------------- final-review fixes

def test_error_row_fails_acceptance_despite_perfect_coverage():
    rows = [row(p, m) for p, m in GOOD.items()]
    rows.append(row("p2", status="error", decision=None, error="boom"))
    s = report.summarise(rows, PROFILES)
    by = {i["id"]: i for i in s["acceptance"]["items"]}
    assert by["coverage_non_sparse"]["status"] == "pass"
    assert by["coverage_sparse"]["status"] == "pass"
    assert by["no_errors_or_skips"]["status"] == "fail"
    assert "1 error" in by["no_errors_or_skips"]["detail"]
    assert s["acceptance"]["passed"] is False


def test_skip_row_fails_no_errors_or_skips():
    rows = [row(p, m) for p, m in GOOD.items()] + [row("p1", status="skip", decision="skip")]
    by = {i["id"]: i for i in report.summarise(rows, PROFILES)["acceptance"]["items"]}
    assert by["no_errors_or_skips"]["status"] == "fail"
    assert "1 skip" in by["no_errors_or_skips"]["detail"]


def test_clean_run_passes_no_errors_or_skips():
    by = {i["id"]: i for i in report.summarise(
        [row(p, m) for p, m in GOOD.items()], PROFILES)["acceptance"]["items"]}
    assert by["no_errors_or_skips"]["status"] == "pass"
    assert by["no_errors_or_skips"]["detail"] == "0 errors, 0 skips"


def test_filter_violation_item_labelled_by_construction():
    items = report.summarise([row("p1", GOOD["p1"])], PROFILES)["acceptance"]["items"]
    (it,) = [i for i in items if i["id"] == "zero_filter_violations"]
    assert "by construction for the new pipeline (same filter); meaningful for legacy" \
        in it["label"]


def test_report_header_shows_requested_model(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "load_profiles", lambda: PROFILES)
    a = tmp_path / "a.jsonl"
    _write(a, [dict(row(p, m), backend="claude-cli", model="sonnet")
               for p, m in GOOD.items()])
    report.main([str(a), "--out", str(tmp_path / "r")])
    for ext in ("md", "html"):
        head = (tmp_path / f"r.{ext}").read_text().split("Summary")[0]
        assert "claude-cli" in head and "sonnet" in head
    assert report.summarise(_rows(a), PROFILES)["model"] == "sonnet"


def test_run_records_requested_model(run_env, monkeypatch):
    from evals.openers.cli_backends import ClaudeCli
    fake = FakeBackend(out=_new_out)
    fake.model = "sonnet"
    monkeypatch.setattr(run, "make_backend", lambda name, model: fake)
    out = run_env / "m.jsonl"
    run.main(["--backend", "claude-cli", "--pipeline", "new", "--limit", "1",
              "--out", str(out)])
    assert _rows(out)[0]["model"] == "sonnet"
    assert run.requested_model("claude-cli", ClaudeCli(None)) == "sonnet"
    assert run.requested_model("anthropic", object()) == config.MODEL
    assert run.requested_model("ollama", object()) == config.OLLAMA_MODEL
    assert run.requested_model("codex-cli", object()) == "codex default"
