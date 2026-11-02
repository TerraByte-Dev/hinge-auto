"""target: prompt-label parsing, scoring and heart selection (spec §6)."""

import config
import target
import ui

LABEL = "Prompt: This year, I really want to. Answer: Explore......."
ANCHOR = {"id": "f2", "kind": "prompt", "index": 1,
          "prompt_text": "This year, I really want to",
          "answer_text": "Explore more of the coast", "detail": ""}


def N(label, y0, y1, x0=40, x1=1040):
    return ui.UiNode(label, x0, y0, x1, y1)


def test_config_values():
    assert config.TARGET == {"prompt_min_score": 0.8, "question_min_ratio": 0.9,
                             "max_scroll_steps": 7, "low_y_frac": 0.55}


def test_normalise():
    assert target.normalise("  Explore......  ") == "explore"
    assert target.normalise("Go to the  beach…") == "go to the beach"
    assert target.normalise("Hello, World!") == "hello world"


def test_curly_apostrophe_matches_straight():
    # Review Focus 3
    assert target.normalise("I’m") == target.normalise("I'm")
    lbl = "Prompt: I’m weirdly attracted to. Answer: People who’re kind"
    assert target.match_score(lbl, "I'm weirdly attracted to", "People who're kind") >= 0.99


def test_parse_prompt_label():
    assert target.parse_prompt_label(LABEL) == ("This year, I really want to", "Explore.......")
    assert target.parse_prompt_label("Like prompt") is None
    assert target.parse_prompt_label(None) is None


def test_truncated_answer_matches():
    assert target.match_score(LABEL, ANCHOR["prompt_text"], ANCHOR["answer_text"]) >= 0.8


def test_different_prompt_same_answer_below_threshold():
    other = "Prompt: My simple pleasures. Answer: Explore......."
    assert target.match_score(other, ANCHOR["prompt_text"], ANCHOR["answer_text"]) < 0.8


def test_missing_answer_text_cannot_reach_threshold():
    assert target.match_score(LABEL, ANCHOR["prompt_text"], "") < 0.8


def test_heart_below_matched_prompt_and_above_next():
    other = "Prompt: My simple pleasures. Answer: Coffee"
    nodes = [N(other, 200, 600), N("Like prompt", 480, 580, 900, 1000),
             N(LABEL, 700, 1200), N("Like prompt", 1080, 1180, 900, 1000),
             N("Prompt: Dating me is like. Answer: Fun", 1300, 1700),
             N("Like prompt", 1580, 1680, 900, 1000)]

    prompt, heart, score = target.find_prompt_heart(nodes, ANCHOR)

    assert prompt.label == LABEL and heart.y0 == 1080 and score >= 0.8


def test_matched_card_heart_offscreen_is_none_not_neighbour():
    nodes = [N(LABEL, 1800, 2120),
             N("Prompt: Dating me is like. Answer: Fun", 2130, 2200)]
    assert target.find_prompt_heart(nodes, ANCHOR) is None


def test_heart_past_a_photo_is_not_taken():
    nodes = [N(LABEL, 1500, 1900), N("Like photo", 2000, 2100, 900, 1000),
             N("Like prompt", 2150, 2200, 900, 1000)]
    assert target.find_prompt_heart(nodes, ANCHOR) is None


def test_lone_like_prompt_without_matched_prompt_is_none():
    assert target.find_prompt_heart([N("Like prompt", 1080, 1180, 900, 1000)], ANCHOR) is None
    other = "Prompt: My simple pleasures. Answer: Coffee"
    assert target.find_prompt_heart([N(other, 700, 1200),
                                     N("Like prompt", 1080, 1180, 900, 1000)], ANCHOR) is None


def test_no_anchor_is_none():
    assert target.find_prompt_heart([N(LABEL, 700, 1200)], None) is None


# ---------- fix round 1 (ruling R16): question floor ----------

def _heart_for(label, anchor):
    return target.find_prompt_heart([N(label, 700, 1200),
                                     N("Like prompt", 1080, 1180, 900, 1000)], anchor)


def _anchor(q, a):
    return dict(ANCHOR, prompt_text=q, answer_text=a)


def test_near_miss_question_rejected_despite_score():
    # Probe: total score 0.815 >= 0.8, but it's a different prompt.
    label = "Prompt: I’m known for. Answer: Someone......."
    anchor = _anchor("I’m looking for", "Someone kind who loves the outdoors")
    assert target.match_score(label, anchor["prompt_text"], anchor["answer_text"]) >= 0.8
    assert target.accept(label, anchor["prompt_text"], anchor["answer_text"]) is None
    assert _heart_for(label, anchor) is None


def test_i_am_known_for_vs_looking_for_rejected():
    label = "Prompt: I am known for. Answer: Someone......."
    assert _heart_for(label, _anchor("I am looking for", "Someone kind")) is None


def test_exact_question_truncated_answer_accepted():
    found = _heart_for(LABEL, ANCHOR)
    assert found is not None and found[2] >= 0.8


def test_curly_vs_straight_apostrophe_exact_question_accepted():
    label = "Prompt: I’m looking for. Answer: Someone......."
    found = _heart_for(label, _anchor("I'm looking for", "Someone kind"))
    assert found is not None and found[0].label == label
