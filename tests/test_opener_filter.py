"""Deterministic opener filter (opener.check_opener)."""
import pytest

import opener
from opener import OpenerHistory, check_opener, resolve_fact

FACTS = [
    {"id": "f1", "kind": "prompt", "index": 1, "prompt_text": "Typical Sunday",
     "answer_text": "run if my legs work, market if they don't, nap either way",
     "detail": "sunday routine"},
    {"id": "f2", "kind": "bio", "index": 0, "prompt_text": "",
     "answer_text": "", "detail": "loves hiking and climbing"},
]


def cand(text, fact_id="f1", form="observation"):
    return {"text": text, "fact_id": fact_id, "form": form}


CASES = [
    ("empty", cand("")),
    ("non_ascii", cand("the nap thing is a vibe — respect")),
    ("unsafe_char", cand('the "nap" part is the right call')),
    ("too_long", cand("the nap " + "being the best part of sunday " * 6)),
    ("too_short", cand("nap time")),
    ("generic", cand("nap either way, tell me more about sunday")),
    ("cringe", cand("is the market where you fell from heaven on sunday")),
    ("pressure", cand("you owe me a nap recommendation for sunday")),
    ("body_remark", cand("sunday runs must be good for that hot body")),
    ("ai_tell", cand("the nap either way part is genuinely the right call")),
    ("pickup_line", cand("are you a market because nap either way sounds good")),
    ("shared_experience", cand("nap either way, me too on most sundays")),
    ("appearance", cand("a stunning sunday routine with a nap in it")),
    ("multi_question", cand("nap either way? market too? run when?")),
    ("bad_fact", cand("the nap being non-negotiable is right", fact_id="f9")),
    ("bad_form", cand("the nap being non-negotiable is right", form="pun")),
    ("ungrounded", cand("pineapple on pizza is a fair debate to have")),
    ("repeat", cand("ok the nap again is the right call")),
]


@pytest.mark.parametrize("code,obj", CASES, ids=[c for c, _ in CASES])
def test_reason_code(code, obj):
    h = OpenerHistory()
    h.add("ok the nap thing is the right call")
    assert code in check_opener(obj, FACTS, h)


def test_clean_statement_passes():
    c = cand("the nap being non-negotiable either way is the right call")
    assert check_opener(c, FACTS, OpenerHistory()) == []


def test_three_questions_rejected():
    c = cand("nap either way? market too? run when?")
    assert "multi_question" in check_opener(c, FACTS, OpenerHistory())


def test_reasons_unique_and_ordered():
    c = cand("tell me more, honestly gorgeous?? sure??")
    r = check_opener(c, FACTS, OpenerHistory())
    assert len(r) == len(set(r))
    order = [x for x, _ in CASES]
    assert r == sorted(r, key=order.index)


def test_fact_id_drift_normalised():
    t = "the nap being non-negotiable either way is the right call"
    h = OpenerHistory()
    assert check_opener(cand(t, fact_id=" F1 "), FACTS, h) == []
    assert check_opener(cand(t, fact_id=1), FACTS, h) == []
    assert resolve_fact(" F1 ", FACTS)["id"] == "f1"
    assert resolve_fact(1, FACTS)["id"] == "f1"
    assert resolve_fact(2, FACTS)["id"] == "f2"
    assert resolve_fact(7, FACTS) is None
    assert resolve_fact(None, FACTS) is None
    assert resolve_fact(True, FACTS) is None


def test_ungrounded_not_reported_when_fact_missing():
    r = check_opener(cand("pineapple on pizza is a fair debate", fact_id="zz"),
                     FACTS, OpenerHistory())
    assert "bad_fact" in r and "ungrounded" not in r


def test_sender_experience_can_pass_filter():
    # Only stage 2 catches invented sender biography.
    c = cand("I spent last summer hiking in Peru", fact_id="f2")
    assert check_opener(c, FACTS, OpenerHistory()) == []


def test_fellow_needs_following_word():
    ok = cand("the fellowship of the nap either way is real")
    assert "ai_tell" not in check_opener(ok, FACTS, OpenerHistory())
    bad = cand("fellow runner here, nap either way")
    assert "ai_tell" in check_opener(bad, FACTS, OpenerHistory())


def test_history_repeat():
    h = OpenerHistory()
    h.add("ok the nap thing")
    assert h.starts_like("OK the nap again")
    assert "repeat" in check_opener(cand("ok the nap again"), FACTS, h)


def test_repeat_needs_three_words():
    h = OpenerHistory()
    h.add("nap time")
    assert not h.starts_like("nap time")


def test_history_maxlen():
    h = OpenerHistory(maxlen=2)
    for t in ("aa bb cc", "dd ee ff", "gg hh ii"):
        h.add(t)
    assert not h.starts_like("aa bb cc x")
    assert h.starts_like("gg hh ii x")
