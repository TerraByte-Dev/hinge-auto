"""Stage-2 finalize (opener.finalize) with a fake complete_json. No network."""
import re

import pytest

import config
import opener
from judge_common import Decision
from opener import OpenerHistory, finalize

FACTS = [
    {"id": "f1", "kind": "prompt", "index": 1, "prompt_text": "Typical Sunday",
     "answer_text": "run if my legs work, market if they don't, nap either way",
     "detail": "sunday routine"},
    {"id": "f2", "kind": "bio", "index": 0, "prompt_text": "",
     "answer_text": "", "detail": "loves hiking and climbing"},
    {"id": "f3", "kind": "photo", "index": 2, "prompt_text": "",
     "answer_text": "", "detail": "holding a sourdough loaf in a kitchen"},
]

GOOD0 = {"text": "nap either way is a strong sunday policy", "fact_id": "f1",
         "form": "observation"}
GOOD1 = {"text": "that sourdough loaf looks like a weekend project",
         "fact_id": "f3", "form": "observation"}
BANNED = {"text": "the nap either way part is genuinely the right call",
          "fact_id": "f1", "form": "observation"}
PERU = {"text": "I spent last summer hiking in Peru, best trip ever",
        "fact_id": "f2", "form": "observation"}


def like(candidates, pick_index=0, facts=FACTS, **kw):
    d = Decision(name="Ana", decision="like", confidence="high",
                 reasoning="r", message="stage-1 free text never sent",
                 message_archetype="observation_question",
                 profile_facts=[dict(f) for f in facts],
                 candidates=[dict(c) for c in candidates],
                 pick_index=pick_index,
                 anchor={"id": "model-supplied"},
                 usage={"input_tokens": 100, "output_tokens": 10})
    for k, v in kw.items():
        setattr(d, k, v)
    return d


class FakeSelect:
    """Records calls; returns scripted dicts (or raises Exception items)."""

    def __init__(self, *responses, on_call=None):
        self.responses = list(responses)
        self.calls = []
        self.on_call = on_call

    def __call__(self, system, user, schema, name):
        self.calls.append({"system": system, "user": user, "schema": schema,
                           "name": name})
        if self.on_call:
            self.on_call(len(self.calls))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return dict(r)


def assert_empty(d):
    assert d.message == ""
    assert d.message_archetype == "empty"
    assert d.anchor is None
    assert d.prompt_referenced == ""
    assert d.premade_id == ""
    assert d.decision == "like"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(config, "OPENER_SELECT", True, raising=False)
    monkeypatch.setattr(config, "OPENER_RETRY", 1, raising=False)


def test_config_defaults():
    """Shipped defaults, read from source (the autouse fixture patches them)."""
    import ast
    tree = ast.parse(open(config.__file__).read())
    vals = {t.id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) for t in n.targets
            if isinstance(t, ast.Name) and t.id.startswith("OPENER_")}
    assert vals == {"OPENER_CANDIDATES": 3, "OPENER_RETRY": 1,
                    "OPENER_SELECT": True}


def test_skip_untouched():
    d = Decision(name="Bo", decision="skip", confidence="high", reasoning="r",
                 skip_reason="other", message="", candidates=[GOOD0])
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "skip"
    assert fake.calls == []
    assert d.message == "" and d.decision == "skip"


def test_empty_facts():
    d = like([GOOD0], facts=[])
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "empty_no_facts"
    assert_empty(d)
    assert fake.calls == []


def test_empty_candidates():
    d = like([], pick_index=-1)
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "empty_no_candidates"
    assert_empty(d)
    assert fake.calls == []


@pytest.mark.parametrize("select", [True, False])
def test_pick_minus_one_select_off(monkeypatch, select):
    monkeypatch.setattr(config, "OPENER_SELECT", select)
    d = like([GOOD0, GOOD1], pick_index=-1)
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "pick_invalid"
    assert_empty(d)
    assert fake.calls == []


def test_chosen_writes_contract():
    d = like([GOOD0, GOOD1], pick_index=0, premade_id="bogus")
    fake = FakeSelect({"choice_index": 1, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert d.message == GOOD1["text"]
    assert d.message_archetype == "observation"
    assert d.anchor == FACTS[2]
    assert d.prompt_referenced == "photo 2"
    assert d.premade_id == ""
    assert len(fake.calls) == 1
    assert fake.calls[0]["name"] and fake.calls[0]["schema"] is not None


def test_chosen_prompt_fact_prompt_referenced():
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert d.anchor == FACTS[0]
    assert d.prompt_referenced == "Typical Sunday"


def test_survivor_renumbering():
    d = like([BANNED, GOOD1])
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert d.message == GOOD1["text"]
    user = fake.calls[0]["user"]
    lines = user.splitlines()
    good_line = next(l for l in lines if GOOD1["text"] in l)
    bad_line = next(l for l in lines if BANNED["text"] in l)
    assert re.match(r"\s*0\.", good_line)
    assert not re.match(r"\s*\d", bad_line)
    assert "ai_tell" in bad_line
    assert user.index("not selectable") < user.index(BANNED["text"])

    d = like([BANNED, GOOD1])
    fake = FakeSelect({"choice_index": 1, "rewrite": None, "reason": "x"})
    assert finalize(d, fake, OpenerHistory()) == "stage2_invalid_choice"
    assert_empty(d)


@pytest.mark.parametrize("idx", [-2, 5, True, False, None, "1.5", "abc", "0x0",
                                 "", 0.0, "5", "٠"])
def test_bad_indices_empty(idx):
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": idx, "rewrite": None, "reason": "x"})
    assert finalize(d, fake, OpenerHistory()) in ("stage2_invalid_choice",
                                                    "stage2_error")
    assert_empty(d)


@pytest.mark.parametrize("idx, want", [("0", GOOD0), (" 1 ", GOOD1), ("1", GOOD1)])
def test_numeric_string_choice_index_accepted(idx, want):
    """R25: like R7 (pick_index via int()), stage-2 numeric strings resolve."""
    d = like([GOOD0, GOOD1])
    fake = FakeSelect({"choice_index": idx, "rewrite": None, "reason": "x"})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert d.message == want["text"]


def test_chosen_revalidated():
    """The survivor is mutated (to a banned phrase) after filtering, while
    stage 2 is running; the pre-writeback re-check must catch it."""
    d = like([GOOD0])

    def mutate(_n):
        d.candidates[0]["text"] = BANNED["text"]

    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"},
                      on_call=mutate)
    assert finalize(d, fake, OpenerHistory()) == "stage2_invalid_choice"
    assert_empty(d)


def test_chosen_revalidated_runs_validate(monkeypatch):
    seen = []
    real = opener._validate

    def spy(obj, facts, history):
        seen.append(obj["text"])
        return real(obj, facts, history)

    monkeypatch.setattr(opener, "_validate", spy)
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert seen == [GOOD0["text"]]


REWRITE = {"text": "a nap either way rule beats any sunday plan",
           "fact_id": "f1", "form": "tease"}


def test_rewrite_once_then_rejudge():
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": -1, "rewrite": REWRITE, "reason": "meh"},
                      {"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "rewritten"
    assert len(fake.calls) == 2
    assert d.message == REWRITE["text"]
    assert d.message_archetype == "tease"
    assert d.anchor == FACTS[0]
    rejudge = fake.calls[1]
    assert re.search(r"^\s*0\..*" + re.escape(REWRITE["text"]), rejudge["user"],
                     re.M)
    assert GOOD0["text"] not in rejudge["user"].split("not selectable")[0]
    assert rejudge["schema"]["properties"]["rewrite"] == {"type": "null"}

    # A second rewrite request on the rejudge is ignored -> empty, no 3rd call.
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": -1, "rewrite": REWRITE, "reason": "meh"},
                      {"choice_index": -1, "rewrite": GOOD1, "reason": "again"},
                      {"choice_index": 0, "rewrite": None, "reason": "never"})
    assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
    assert len(fake.calls) == 2
    assert_empty(d)


def test_rewrite_failing_filter_not_sent():
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": -1, "rewrite": BANNED, "reason": "meh"},
                      {"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
    assert len(fake.calls) == 1
    assert_empty(d)


def test_no_survivors_requests_rewrite():
    d = like([BANNED])
    fake = FakeSelect({"choice_index": -1, "rewrite": REWRITE, "reason": "fix"},
                      {"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "rewritten"
    assert "must be -1" in fake.calls[0]["user"]


def test_no_survivors_statuses():
    """Zero filter survivors -> empty_all_rejected whatever the rewrite-only
    call returns, unless a filter-passing rewrite is then vetoed on rejudge."""
    for resp in ({"choice_index": -1, "rewrite": None, "reason": "none"},
                 {"choice_index": -1, "rewrite": BANNED, "reason": "bad"}):
        d = like([BANNED])
        fake = FakeSelect(resp)
        assert finalize(d, fake, OpenerHistory()) == "empty_all_rejected"
        assert len(fake.calls) == 1
        assert_empty(d)

    d = like([BANNED])
    fake = FakeSelect({"choice_index": -1, "rewrite": REWRITE, "reason": "fix"},
                      {"choice_index": -1, "rewrite": None, "reason": "veto"})
    assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
    assert len(fake.calls) == 2
    assert_empty(d)


def test_survivor_vetoed_statuses():
    """>=1 survivor vetoed -> empty_stage2_rejected, incl. a failing rewrite
    (keeps its originating case) and a vetoed rejudge."""
    for resp in ({"choice_index": -1, "rewrite": None, "reason": "veto"},
                 {"choice_index": -1, "rewrite": BANNED, "reason": "bad"}):
        d = like([GOOD0, BANNED])
        fake = FakeSelect(resp)
        assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
        assert_empty(d)

    d = like([GOOD0])
    fake = FakeSelect({"choice_index": -1, "rewrite": REWRITE, "reason": "meh"},
                      {"choice_index": -1, "rewrite": None, "reason": "veto"})
    assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
    assert_empty(d)


def test_status_list_documented():
    for s in ("premade", "chosen", "rewritten", "empty_no_facts",
              "empty_no_candidates", "empty_all_rejected",
              "empty_stage2_rejected", "stage2_error", "stage2_invalid_choice",
              "pick_invalid", "select_off", "skip"):
        assert s in finalize.__doc__


def test_no_survivors_retry_zero_no_call(monkeypatch):
    monkeypatch.setattr(config, "OPENER_RETRY", 0)
    d = like([BANNED])
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "empty_all_rejected"
    assert fake.calls == []
    assert_empty(d)


def test_retry_zero_rewrite_ignored(monkeypatch):
    monkeypatch.setattr(config, "OPENER_RETRY", 0)
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": -1, "rewrite": REWRITE, "reason": "x"})
    assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
    assert len(fake.calls) == 1
    assert fake.calls[0]["schema"]["properties"]["rewrite"] == {"type": "null"}


@pytest.mark.parametrize("bad", [RuntimeError("boom"), "not a dict"])
def test_stage2_exception_empty(bad):
    d = like([GOOD0])
    if isinstance(bad, Exception):
        fake = FakeSelect(bad)
    else:
        def fake(*_a):
            return bad
    assert finalize(d, fake, OpenerHistory()) == "stage2_error"
    assert_empty(d)


def test_sender_experience_needs_stage2():
    assert opener.check_opener(PERU, FACTS, None) == []  # filter alone passes
    d = like([PERU])
    fake = FakeSelect({"choice_index": -1, "rewrite": None,
                       "reason": "invents sender biography"})
    assert finalize(d, fake, OpenerHistory()) == "empty_stage2_rejected"
    assert_empty(d)

    d = like([PERU])
    assert finalize(d, None, OpenerHistory()) == "stage2_error"
    assert_empty(d)


def test_select_off_uses_original_index(monkeypatch):
    monkeypatch.setattr(config, "OPENER_SELECT", False)
    d = like([BANNED, GOOD1], pick_index=1)
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "select_off"
    assert d.message == GOOD1["text"]
    assert d.anchor == FACTS[2]
    assert d.message_archetype == "observation"
    assert fake.calls == []

    d = like([BANNED, GOOD1], pick_index=0)
    assert finalize(d, fake, OpenerHistory()) == "pick_invalid"
    assert_empty(d)
    assert fake.calls == []

    d = like([GOOD0], pick_index=3)
    assert finalize(d, None, OpenerHistory()) == "pick_invalid"
    assert_empty(d)


def test_history_only_sent():
    h = OpenerHistory()
    d = like([GOOD0, GOOD1])
    fake = FakeSelect({"choice_index": -1, "rewrite": None, "reason": "no"})
    assert finalize(d, fake, h) == "empty_stage2_rejected"
    assert not h.starts_like(GOOD0["text"])
    assert not h.starts_like(GOOD1["text"])

    d = like([GOOD0, GOOD1])
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, h) == "chosen"
    assert h.starts_like(GOOD0["text"])
    assert not h.starts_like(GOOD1["text"])


def test_usage_accumulates():
    d = like([GOOD0])
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok",
                       "_usage": {"input_tokens": 50, "output_tokens": 7}})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert d.usage["input_tokens"] == 150
    assert d.usage["output_tokens"] == 17


def test_valid_premade_wins(monkeypatch):
    import judge_common
    p = {"id": "p1", "message": "hey, your sunday sounds solid", "use_when": "x"}
    monkeypatch.setattr(judge_common, "valid_premade",
                        lambda pid: p if pid == "p1" else None, raising=False)
    d = like([GOOD0], premade_id="p1", message="drifted text")
    fake = FakeSelect()
    assert finalize(d, fake, OpenerHistory()) == "premade"
    assert d.message == p["message"]
    assert d.message_archetype == "premade"
    assert d.anchor is None and d.prompt_referenced == ""
    assert d.premade_id == "p1"
    assert fake.calls == []


def test_invalid_premade_cleared_normal_path():
    d = like([GOOD0], premade_id="nope")
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"})
    assert finalize(d, fake, OpenerHistory()) == "chosen"
    assert d.premade_id == ""


def test_select_schema_shape():
    s = opener.SELECT_SCHEMA
    assert set(s["required"]) >= {"choice_index", "rewrite", "reason"}
    assert s["properties"]["choice_index"]["type"] == "integer"
    import inspect
    src = inspect.getsource(opener)
    assert "consent-and-safety" in src and "persona-style-transfer" in src
    assert "sender biography" in opener.SELECT_SYSTEM


# ---- backend complete_json (monkeypatched clients, no network) -------------

class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_anthropic_complete_json(monkeypatch):
    import anthropic
    import judge
    sent = {}

    class FakeMessages:
        def create(self, **kw):
            sent.update(kw)
            return _Blk(content=[_Blk(type="text", text="hi"),
                                 _Blk(type="tool_use", name="select_opener",
                                      input={"choice_index": 0, "rewrite": None,
                                             "reason": "ok"})],
                        usage=_Blk(input_tokens=11, output_tokens=3),
                        stop_reason="tool_use")

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    schema = {"type": "object", "properties": {}}
    out = judge.complete_json("SYS", "USER", schema, "select_opener")
    assert out == {"choice_index": 0, "rewrite": None, "reason": "ok",
                   "_usage": {"input_tokens": 11, "output_tokens": 3}}
    assert sent["model"] == config.MODEL
    assert sent["max_tokens"] == 800
    assert sent["tool_choice"] == {"type": "tool", "name": "select_opener"}
    assert sent["tools"][0]["name"] == "select_opener"
    assert sent["tools"][0]["input_schema"] is schema
    assert sent["system"] == "SYS"
    assert sent["messages"] == [{"role": "user", "content": "USER"}]
    assert "output_config" not in sent


def test_anthropic_complete_json_no_tool_raises(monkeypatch):
    import anthropic
    import judge

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = _Blk(create=lambda **kw: _Blk(
                content=[], usage=_Blk(input_tokens=1, output_tokens=1),
                stop_reason="max_tokens"))

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    with pytest.raises(RuntimeError):
        judge.complete_json("S", "U", {}, "select_opener")


def test_ollama_complete_json(monkeypatch):
    import judge_ollama
    sent = {}

    class FakeClient:
        def chat(self, **kw):
            sent.update(kw)
            return {"message": {"content": '{"choice_index": -1, "rewrite": null,'
                                           ' "reason": "no"}'},
                    "prompt_eval_count": 20, "eval_count": 4}

    monkeypatch.setattr(judge_ollama, "_client", lambda: FakeClient())
    monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen-test")
    schema = {"type": "object"}
    out = judge_ollama.complete_json("SYS", "USER", schema, "select_opener")
    assert out == {"choice_index": -1, "rewrite": None, "reason": "no",
                   "_usage": {"input_tokens": 20, "output_tokens": 4}}
    assert sent["model"] == "qwen-test"
    assert sent["format"] is schema
    assert sent["options"] == {"temperature": 0.2}
    assert sent["messages"] == [{"role": "system", "content": "SYS"},
                                {"role": "user", "content": "USER"}]


# ---------- opener_trace: what finalize saw, filtered and chose ----------

def test_trace_records_candidates_verdicts_and_stage2_reason():
    d = like([GOOD0, BANNED, GOOD1], pick_index=1)
    sel = FakeSelect({"choice_index": 1, "rewrite": None, "reason": "most specific"})

    assert finalize(d, sel, OpenerHistory()) == "chosen"

    t = d.opener_trace
    assert t["status"] == "chosen"
    assert [c["text"] for c in t["candidates"]] == [GOOD0["text"], BANNED["text"], GOOD1["text"]]
    assert [c["stage1_pick"] for c in t["candidates"]] == [False, True, False]
    assert t["candidates"][0]["reject"] == [] and "ai_tell" in t["candidates"][1]["reject"]
    assert t["stage2"] == [{"choice_index": 1, "reason": "most specific", "rewrite": None}]
    assert t["final"] == GOOD1["text"] == d.message


def test_trace_records_rewrite_and_its_verdict():
    rw = {"text": "run if my legs work is a good sunday plan", "fact_id": "f1",
          "form": "observation"}
    d = like([BANNED], pick_index=0)
    sel = FakeSelect({"choice_index": -1, "rewrite": rw, "reason": "fixed tell"},
                     {"choice_index": 0, "rewrite": None, "reason": "ok"})

    assert finalize(d, sel, OpenerHistory()) == "rewritten"

    s1, s2 = d.opener_trace["stage2"]
    assert s1["rewrite"]["text"] == rw["text"] and s1["rewrite_reject"] == []
    assert s2["reason"] == "ok"
    assert d.opener_trace["final"] == rw["text"]


def test_trace_on_stage2_error_and_skip_and_premade(monkeypatch):
    d = like([GOOD0])
    assert finalize(d, FakeSelect(RuntimeError("down")), OpenerHistory()) == "stage2_error"
    assert d.opener_trace["status"] == "stage2_error"
    assert d.opener_trace["stage2"] == [{"error": "RuntimeError: down"}]
    assert d.opener_trace["final"] == ""

    s = like([], decision="skip")
    assert finalize(s, None, None) == "skip"
    assert s.opener_trace["status"] == "skip" and s.opener_trace["candidates"] == []


def test_trace_select_off_records_verdicts(monkeypatch):
    monkeypatch.setattr(config, "OPENER_SELECT", False)
    d = like([GOOD0, BANNED], pick_index=0)

    assert finalize(d, None, OpenerHistory()) == "select_off"
    assert [bool(c["reject"]) for c in d.opener_trace["candidates"]] == [False, True]
    assert d.opener_trace["stage2"] == [] and d.opener_trace["final"] == GOOD0["text"]
