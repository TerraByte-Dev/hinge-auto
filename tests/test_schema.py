import json

from judge_common import (
    DECIDE_INPUT_SCHEMA, FACT_KINDS, OPENER_FORMS, Decision,
    decision_from_dict,
)

LEGACY = {
    "name": "sam", "decision": "like", "confidence": "high",
    "reasoning": "r", "message": "hi", "skip_reason": "none",
    "message_archetype": "other", "premade_id": "", "prompt_referenced": "",
}


def test_old_dict_loads():
    d = decision_from_dict(dict(LEGACY), {"input_tokens": 1})
    assert isinstance(d, Decision)
    assert d.profile_facts == []
    assert d.candidates == []
    assert d.pick_index == -1
    assert d.anchor is None
    assert d.usage == {"input_tokens": 1}


def test_unknown_keys_dropped():
    d = decision_from_dict({**LEGACY, "foo": 1}, {})
    assert d.name == "sam"


def test_string_encoded_lists_parsed():
    cands = '[{"text":"a","fact_id":"f1","form":"observation"}]'
    d = decision_from_dict({**LEGACY, "candidates": cands}, {})
    assert len(d.candidates) == 1 and d.candidates[0]["text"] == "a"
    facts = json.dumps([{"id": "f1", "kind": "bio"}])
    d = decision_from_dict({**LEGACY, "profile_facts": facts}, {})
    assert d.profile_facts == [{"id": "f1", "kind": "bio"}]
    d = decision_from_dict({**LEGACY, "candidates": "not json"}, {})
    assert d.candidates == []
    d = decision_from_dict({**LEGACY, "candidates": '{"a": 1}'}, {})
    assert d.candidates == []


def test_pick_index_coerced():
    assert decision_from_dict({**LEGACY, "pick_index": "2"}, {}).pick_index == 2
    assert decision_from_dict({**LEGACY, "pick_index": "x"}, {}).pick_index == -1


def test_schema_has_new_required_fields():
    req = DECIDE_INPUT_SCHEMA["required"]
    for k in ("profile_facts", "candidates", "pick_index"):
        assert k in req
    enum = DECIDE_INPUT_SCHEMA["properties"]["message_archetype"]["enum"]
    for v in ("observation", "either_or", "question", "observation_question"):
        assert v in enum
    props = DECIDE_INPUT_SCHEMA["properties"]
    assert props["profile_facts"]["maxItems"] == 8
    assert props["candidates"]["maxItems"] == 4
    assert props["profile_facts"]["items"]["properties"]["kind"]["enum"] == list(FACT_KINDS)
    assert props["candidates"]["items"]["properties"]["form"]["enum"] == list(OPENER_FORMS)


def test_bad_enums_clamped():
    d = decision_from_dict({**LEGACY, "decision": "maybe", "confidence": "x"}, {})
    assert d.decision == "skip"
    assert d.confidence == "low"


def test_missing_legacy_key_warns(capsys):
    d = {k: v for k, v in LEGACY.items() if k != "decision"}
    out = decision_from_dict(d, {})
    assert out.decision == "skip"
    cap = capsys.readouterr().out
    assert "[judge] warning" in cap and "decision" in cap


def test_no_warning_without_new_fields(capsys):
    decision_from_dict(dict(LEGACY), {})
    assert capsys.readouterr().out == ""


def test_missing_legacy_keys_get_defaults():
    d = decision_from_dict({}, {})
    assert d.name == "unknown" and d.decision == "skip"
    assert d.confidence == "low" and d.skip_reason == "other"
    assert d.message_archetype == "empty" and d.message == ""
