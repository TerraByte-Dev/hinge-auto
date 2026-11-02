"""System prompt: voice rewrite, facts/candidates section, premade sanitation."""
import re

import pytest

import config
import judge_common
import modes
import opener
from judge_common import Decision, build_system_prompt, valid_premade

from tests.test_opener_finalize import FakeSelect, like, GOOD0


@pytest.fixture
def no_premades(monkeypatch):
    monkeypatch.setattr(config, "PREMADES", [])


@pytest.mark.parametrize("voice", [None, "example_casual", "example_polished"])
def test_prompt_has_no_question_mandate(monkeypatch, no_premades, voice):
    monkeypatch.setattr(config, "MESSAGE_VOICE", voice)
    p = build_system_prompt()
    assert "followed by a short question" not in p
    assert "then a short question" not in p
    assert "at most one question" in p
    assert "attached to photo 1" in p


@pytest.mark.parametrize("voice", [None, "example_casual", "example_polished"])
def test_voice_text_is_typing_safe(monkeypatch, no_premades, voice):
    monkeypatch.setattr(config, "MESSAGE_VOICE", voice)
    text = judge_common.resolve_voice(voice)
    # examples inside the voice must be ASCII; the rules mention the unsafe
    # characters only as escaped text.
    assert text.isascii()


def test_example_voices_keep_tone(monkeypatch):
    casual = judge_common.resolve_voice("example_casual")
    polished = judge_common.resolve_voice("example_polished")
    assert "lowercase" in casual.lower()
    assert "sentence case" in polished.lower()
    assert casual != polished


def test_candidates_section_with_custom_voice(monkeypatch, no_premades):
    monkeypatch.setattr(config, "MESSAGE_VOICE", "line one\nline two\nbe nice")
    monkeypatch.setattr(config, "OPENER_CANDIDATES", 3, raising=False)
    p = build_system_prompt()
    assert "line two" in p
    assert "profile_facts" in p and "candidates" in p
    assert "pick_index" in p and "f1" in p


@pytest.mark.parametrize("n,expect", [(3, "3"), (9, "4"), (1, "1")])
def test_candidate_count_capped(monkeypatch, no_premades, n, expect):
    monkeypatch.setattr(config, "OPENER_CANDIDATES", n, raising=False)
    monkeypatch.setattr(config, "MESSAGE_VOICE", None)
    p = build_system_prompt()
    assert f"up to {expect} candidate" in p


def test_placeholder_premade_omitted(monkeypatch, capsys):
    bad = {"id": "p", "message": "hey [prompt]", "use_when": "x"}
    monkeypatch.setattr(config, "PREMADES", [bad])
    assert valid_premade("p") is None
    assert "hey [prompt]" not in build_system_prompt()
    assert "[judge] warning" in capsys.readouterr().out
    d = Decision(name="a", decision="like", confidence="high", reasoning="r",
                 message="x", premade_id="p", message_archetype="premade")
    judge_common.enforce_premade_verbatim(d)
    assert d.premade_id == ""
    assert d.message_archetype != "premade"


@pytest.mark.parametrize("msg", ['say "hi" now', "back\\slash", "cost $5",
                                 "tick ` mark", "café time friend"])
def test_unsafe_premade_invalid(monkeypatch, msg):
    monkeypatch.setattr(config, "PREMADES",
                        [{"id": "p", "message": msg, "use_when": "x"}])
    assert valid_premade("p") is None


def test_valid_premade_verbatim(monkeypatch):
    p = {"id": "p1", "message": "hey, your sunday sounds solid",
         "use_when": "any"}
    monkeypatch.setattr(config, "PREMADES", [p])
    assert valid_premade("p1") == p
    assert p["message"] in build_system_prompt()
    d = like([GOOD0], premade_id="p1", message="drifted")
    judge_common.enforce_premade_verbatim(d)
    assert d.message == p["message"]
    monkeypatch.setattr(config, "OPENER_SELECT", True, raising=False)
    fake = FakeSelect()
    d = like([GOOD0], premade_id="p1", message="drifted")
    assert opener.finalize(d, fake, opener.OpenerHistory()) == "premade"
    assert d.message == p["message"]
    assert d.message_archetype == "premade"
    assert d.anchor is None
    assert d.prompt_referenced == ""
    assert fake.calls == []


def test_fresh_choice_clears_premade_id(monkeypatch):
    monkeypatch.setattr(config, "PREMADES", [])
    monkeypatch.setattr(config, "OPENER_SELECT", True, raising=False)
    d = like([GOOD0], premade_id="bogus")
    fake = FakeSelect({"choice_index": 0, "rewrite": None, "reason": "ok"})
    opener.finalize(d, fake, opener.OpenerHistory())
    assert d.premade_id == ""


def test_example_lenient_has_no_placeholders():
    assert modes.load("example_lenient").PREMADES == []
