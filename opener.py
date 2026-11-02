"""Opener generation: deterministic first-gate filter for model-written openers.

The regexes `_GENERIC`, `_CRINGE`, `_PRESSURE`, the `_STOPWORDS` set and
`_content_words` below are ported verbatim from OpenDate
(github.com/sumitaich1998/OpenDate @ ba179f23b14a, MIT),
`src/opendate/orchestrator/quality.py`; the body-remark rules adapt
`skills/registry/consent-and-safety/SKILL.md`. License text and the list of
adapted files: third_party/OPENDATE_LICENSE.

Unlike OpenDate's scored critic this is hard pass/fail per rule, returning
reason codes. It is free (no model call); stage 2 does ranking and the
semantic checks (invented sender experience, photo-1 fit) that regexes can't.
"""
import re
from collections import deque

import judge_common

MIN_LEN, MAX_LEN = 12, 140

# --- ported verbatim from OpenDate orchestrator/quality.py (MIT) -------------
_GENERIC = re.compile(
    r"\b(tell me more|how'?s your day|how is your day|how are you doing|"
    r"how'?s it going|how is it going|what'?s up|whats up|nice to meet you|"
    r"what do you do for fun|got any (?:fun )?plans|sounds good|that'?s cool|"
    r"thats cool|just wanted to say hi|get to know (?:you|each other) better|"
    r"hope you'?re having a good)\b",
    re.IGNORECASE,
)

_CRINGE = re.compile(
    r"\b(did it hurt when you fell|from heaven|are you a magician|"
    r"running through my mind|brings all the boys|wifi|"
    r"hey (?:beautiful|gorgeous|sexy|cutie)|marry me|"
    r"you'?re the most beautiful (?:woman|girl|person))\b",
    re.IGNORECASE,
)

_PRESSURE = re.compile(
    r"\b(come on|don'?t ignore me|you owe me|just give me your number|"
    r"why won'?t you|you have to|stop playing)\b",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "is",
    "are", "was", "im", "i", "you", "your", "youre", "me", "my", "we", "they",
    "it", "its", "this", "that", "do", "did", "does", "have", "has", "so",
    "just", "with", "as", "at", "not", "no", "yes", "ok", "okay", "what", "how",
    "when", "where", "why", "who", "can", "will", "would", "should", "could",
    "get", "got", "go", "going", "like", "really", "very", "too", "all", "by",
    "be", "been", "about", "if", "then", "than", "out", "up", "one", "from",
    "into", "more", "some", "any", "what's", "lets", "let", "make", "want",
}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}
# --- end of ported code ------------------------------------------------------


def _rx(*alts: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.IGNORECASE)


# Body remarks (adapted from OpenDate consent-and-safety).
_BODY = _rx(r"sexy", r"hot body", r"body count", r"in bed", r"turn(?:s|ed)? me on",
            r"nudes?", r"hook ?up", r"one night stand")
_AI_TELL = _rx(r"genuinely", r"honestly", r"obsessed", r"iconic", r"underrated",
               r"fellow\s+\w+", r"the way you", r"something about", r"love that")
_PICKUP = _rx(r"are you a", r"did it hurt", r"come here often", r"my place or")
_SHARED = _rx(r"me too", r"i also", r"same here", r"we should",
              r"i love\b.*\btoo", r"as a fellow")
_APPEARANCE = _rx(r"gorgeous", r"beautiful", r"stunning", r"so hot", r"cute smile")

# (code, regex) in reporting order.
_PATTERN_RULES = (
    ("generic", _GENERIC), ("cringe", _CRINGE), ("pressure", _PRESSURE),
    ("body_remark", _BODY), ("ai_tell", _AI_TELL), ("pickup_line", _PICKUP),
    ("shared_experience", _SHARED), ("appearance", _APPEARANCE),
)


def _norm_id(fact_id):
    return str(fact_id).strip().lower()


def resolve_fact(fact_id, facts):
    """Fact for `fact_id`, tolerating case/whitespace drift. An int n means
    the fact with id "f<n>" (the prompt asks for f1..fN, 1-based); a numeric
    string like "1" or " 2 " is treated the same."""
    if fact_id is None or isinstance(fact_id, bool):
        return None
    key = _norm_id(fact_id)
    if isinstance(fact_id, int) or key.isdecimal():
        key = f"f{int(key)}"
    for f in facts or []:
        if isinstance(f, dict) and _norm_id(f.get("id", "")) == key:
            return f
    return None


def _words3(text: str) -> tuple:
    return tuple(re.findall(r"[a-z0-9']+", (text or "").lower())[:3])


class OpenerHistory:
    """Recent sent openers; `starts_like` matches on the first three words."""

    def __init__(self, maxlen: int = 20):
        self._items = deque(maxlen=maxlen)

    def add(self, text: str) -> None:
        self._items.append(_words3(text))

    def starts_like(self, text: str) -> bool:
        w = _words3(text)
        return len(w) == 3 and w in self._items


def _stem_info(w: str) -> tuple[str, str | None]:
    """(stem, stem+"e" if an inflection suffix was stripped else None)."""
    suffixed = False
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            suffixed = True
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    return w, (w + "e" if suffixed else None)


def _stem(w: str) -> str:
    return _stem_info(w)[0]


def _words_match(a: str, b: str) -> bool:
    """Same stem, or a suffixed word's stem+"e" equals the other's stem
    (hiking~hike, loved~love) -- but not wine~win, hope~hop."""
    sa, ea = _stem_info(a)
    sb, eb = _stem_info(b)
    return sa == sb or ea == sb or eb == sa


def _grounded(text: str, source: str) -> bool:
    src = _content_words(source)
    return any(_words_match(w, v) for w in _content_words(text) for v in src)


def check_opener(obj, facts, history) -> list[str]:
    """Reason codes for why `obj` ({text, fact_id, form}) fails; [] = pass."""
    obj = obj if isinstance(obj, dict) else {}
    raw = obj.get("text")
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return ["empty"]
    bad = set()
    if not text.isascii():
        bad.add("non_ascii")
    if any(c in text for c in '\\"$`'):
        bad.add("unsafe_char")
    if len(text) > MAX_LEN:
        bad.add("too_long")
    if len(text) < MIN_LEN:
        bad.add("too_short")
    for code, rx in _PATTERN_RULES:
        if rx.search(text):
            bad.add(code)
    if text.count("?") > 1:
        bad.add("multi_question")
    fact = resolve_fact(obj.get("fact_id"), facts)
    if fact is None:
        bad.add("bad_fact")
    elif not _grounded(text, " ".join(
            str(fact.get(k) or "") for k in ("answer_text", "detail", "prompt_text"))):
        bad.add("ungrounded")
    if obj.get("form") not in judge_common.OPENER_FORMS:
        bad.add("bad_form")
    if history is not None and len(text.split()) >= 3 and history.starts_like(text):
        bad.add("repeat")
    return [c for c in _ORDER if c in bad]


_ORDER = ("empty", "non_ascii", "unsafe_char", "too_long", "too_short",
          "generic", "cringe", "pressure", "body_remark", "ai_tell",
          "pickup_line", "shared_experience", "appearance", "multi_question",
          "bad_fact", "bad_form", "ungrounded", "repeat")
