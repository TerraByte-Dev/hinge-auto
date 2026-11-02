"""Find the heart of the prompt an opener references (spec §6).

Pure matching over UI dump nodes; main.do_like does the scrolling, identity
checks and tapping. On the phone a prompt card shows up in uiautomator as
"Prompt: <question>. Answer: <answer>" (long answers truncated with dots),
followed by a "Like prompt" node for that card's heart.
"""

import difflib
import re

import config
import ui

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                         "“": '"', "”": '"', "„": '"', "′": "'"})
_PROMPT_RE = re.compile(r"\s*Prompt:\s*(.*?)\.?\s+Answer:\s*(.*)\Z", re.S)


def normalise(s: str) -> str:
    """Lowercase; curly quotes to straight; drop trailing truncation
    (dots, ellipses) and all punctuation; collapse whitespace."""
    s = (s or "").translate(_QUOTES).lower()
    s = re.sub(r"[.…\s]+\Z", "", s)          # trailing ....... / …
    s = re.sub(r"[^\w\s]", "", s)                 # punctuation, incl. ' and "
    return " ".join(s.split())


def parse_prompt_label(label) -> tuple[str, str] | None:
    """("<q>", "<a>") from "Prompt: <q>. Answer: <a>", else None."""
    if not isinstance(label, str):
        return None
    m = _PROMPT_RE.match(label)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_score(label, prompt_text, answer_text) -> float:
    """0.6 * question ratio + 0.4 * answer score. The answer scores 1.0 when
    the (truncation-stripped) UI answer is a prefix of, or contains, the
    anchor's answer; otherwise its difflib ratio. Empty sides score 0."""
    parsed = parse_prompt_label(label)
    if parsed is None:
        return 0.0
    ui_q, ui_a = (normalise(p) for p in parsed)
    q, a = normalise(prompt_text or ""), normalise(answer_text or "")
    q_score = _ratio(ui_q, q) if ui_q and q else 0.0
    if not ui_a or not a:
        a_score = 0.0
    elif a.startswith(ui_a) or a in ui_a:
        a_score = 1.0
    else:
        a_score = _ratio(ui_a, a)
    return 0.6 * q_score + 0.4 * a_score


def question_ratio(label, prompt_text) -> float:
    """difflib ratio of the normalised UI question vs the anchor's (0 if
    either is empty or the label isn't a prompt)."""
    parsed = parse_prompt_label(label)
    if parsed is None:
        return 0.0
    ui_q, q = normalise(parsed[0]), normalise(prompt_text or "")
    return _ratio(ui_q, q) if ui_q and q else 0.0


def accept(label, prompt_text, answer_text) -> float | None:
    """The match score if `label` is accepted as the anchor's prompt, else
    None. Both floors must hold: question ratio >= TARGET["question_min_ratio"]
    (real truncated labels keep ~one answer word, so the answer term alone
    can't tell "I'm known for" from "I'm looking for") and total score >=
    TARGET["prompt_min_score"]."""
    t = config.TARGET
    if question_ratio(label, prompt_text) < t["question_min_ratio"]:
        return None
    score = match_score(label, prompt_text, answer_text)
    return score if score >= t["prompt_min_score"] else None


def _is_boundary(n: ui.UiNode) -> bool:
    return n.label.startswith("Prompt:") or n.label == "Like photo"


def find_prompt_heart(nodes, anchor) -> tuple[ui.UiNode, ui.UiNode, float] | None:
    """(prompt node, its "Like prompt" node, score) for the best prompt on
    screen that `accept` takes (question floor and total floor), or None. The heart is the
    nearest "Like prompt" at or below the prompt node's top and above the
    next card boundary (another "Prompt:" or a "Like photo"); if the card's
    own heart isn't on screen, None (never a neighbouring card's heart)."""
    if not anchor:
        return None
    nodes = list(nodes or ())
    best, best_score = None, 0.0
    for n in nodes:
        if not n.label.startswith("Prompt:"):
            continue
        s = accept(n.label, anchor.get("prompt_text"), anchor.get("answer_text"))
        if s is not None and s > best_score:
            best, best_score = n, s
    if best is None:
        return None
    limit = min((n.y0 for n in nodes if n is not best and _is_boundary(n)
                 and n.y0 > best.y0), default=None)
    hearts = [n for n in nodes if n.label == "Like prompt"
              and n.center[1] >= best.y0
              and (limit is None or n.center[1] < limit)]
    if not hearts:
        return None
    return best, min(hearts, key=lambda n: n.center[1]), best_score
