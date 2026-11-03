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

import config
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
               r"fellow\s+\w+", r"the way you", r"something about", r"love that",
               # Stock verdicts the eval showed models leaning on.
               r"very specific",
               r"(?:bold|strong|solid|classic|elite|valid)\s+(?:move|policy|take|combo|choice|call|position)",
               r"great combo", r"sounds like a perfect")
# "Are you a fan of ..." is an ordinary question; only the "are you a X ...
# because" pickup structure is flagged.
_PICKUP = _rx(r"are you a\b[^?.!]{0,40}?\??\s*because", r"did it hurt",
              r"come here often", r"my place or")
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


# ============================================================================
# Stage 2: critique + select (spec §3). One text-only call per attempt.
# ============================================================================

SELECT_NAME = "select_opener"

_OPENER_OBJ_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "fact_id": {"type": "string"},
        "form": {"type": "string", "enum": list(judge_common.OPENER_FORMS)},
    },
    "required": ["text", "fact_id", "form"],
}

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "choice_index": {
            "type": "integer",
            "description": "Index into the SURVIVORS list, or -1 for none.",
        },
        "rewrite": {
            "anyOf": [_OPENER_OBJ_SCHEMA, {"type": "null"}],
            "description": "Only when choice_index is -1: one improved opener, "
                           "or null.",
        },
        "reason": {"type": "string"},
    },
    "required": ["choice_index", "rewrite", "reason"],
}

# Re-judge of a rewrite: same call, but another rewrite is not allowed.
_REJUDGE_SCHEMA = {
    **SELECT_SCHEMA,
    "properties": {**SELECT_SCHEMA["properties"], "rewrite": {"type": "null"}},
}

# Rubric. The honesty / body-remark rules adapt OpenDate (MIT, see module
# docstring) skills/registry/consent-and-safety/SKILL.md; the rewrite rules
# adapt skills/registry/persona-style-transfer/SKILL.md.
SELECT_SYSTEM = """\
You review draft dating-app openers that will be attached to a like on a
Hinge profile, before any match. You see the verified profile facts, the
SURVIVORS (drafts that passed an automatic filter; selectable by index) and,
for context only, drafts the filter rejected (not selectable).

Score every survivor 1-5 on each of:
- grounded: it is about exactly one of the listed facts, and is accurate to it;
- natural: sounds like a relaxed person texting, not a copywriter;
- easy_reply: the recipient can answer it in about five seconds;
- specific: it could only have been written for this profile.

Reject a survivor outright (never choose it) if it:
- invents shared interests or experiences, or claims anything about the
  sender's own life, history, skills or plans (no sender biography);
- makes claims about the recipient that the facts don't support;
- stacks compliments, comments on looks or body, or is sexual;
- reads like a pickup line, a canned line, or an elaborate joke;
- would not read sensibly if it were shown attached to their first photo
  instead of the element it references.

Choose the single best acceptable survivor: choice_index = its index in the
SURVIVORS list. If none is acceptable, choice_index = -1.

Only when choice_index is -1 and a rewrite is allowed, you may give ONE
rewrite {text, fact_id, form}: keep the meaning of the best draft (or write
one plain observation about one fact), never fabricate biography or details
not in the facts, plain ASCII, no double quotes, backslashes, dollar signs or
backticks, 12-140 characters, at most one question mark, fact_id copied from
the facts list. If a rewrite is not allowed, rewrite must be null.

Statements are fine; a question is optional. A like with no comment is a
good outcome when nothing is strong. reason: one short sentence.
"""


def _validate(obj, facts, history) -> bool:
    """Full §2 filter on the object about to be written back (step 5b)."""
    return isinstance(obj, dict) and not check_opener(obj, facts, history)


def _prompt_referenced(fact: dict) -> str:
    kind = fact.get("kind")
    if kind == "prompt":
        pt = str(fact.get("prompt_text") or "").strip()
        return pt[:50] if pt else f"prompt {fact.get('index', '')}".strip()
    if kind == "photo":
        return f"photo {fact.get('index', '')}".strip()
    return "bio"


def _set_empty(decision) -> None:
    decision.message = ""
    decision.message_archetype = "empty"
    decision.anchor = None
    decision.prompt_referenced = ""


def _write_back(decision, obj, facts, history) -> None:
    """§1 contract from a validated opener object (step 6)."""
    fact = resolve_fact(obj.get("fact_id"), facts)
    decision.message = obj["text"].strip()
    decision.message_archetype = obj["form"]
    decision.anchor = dict(fact)
    decision.prompt_referenced = _prompt_referenced(fact)
    decision.premade_id = ""
    if history is not None:
        history.add(decision.message)


def _fmt_fact(f: dict) -> str:
    parts = [f"{f.get('id')} [{f.get('kind')} {f.get('index')}]"]
    for k in ("prompt_text", "answer_text", "detail"):
        if f.get(k):
            parts.append(f"{k}: {f.get(k)}")
    return " | ".join(parts)


def _fmt_obj(o: dict) -> str:
    return f"[{o.get('form')}, {o.get('fact_id')}] {o.get('text')}"


def _select_user(facts, survivors, rejected, allow_rewrite: bool) -> str:
    lines = ["PROFILE FACTS:"]
    lines += [f"- {_fmt_fact(f)}" for f in facts if isinstance(f, dict)]
    lines.append("")
    lines.append("SURVIVORS (selectable; choice_index refers to this list):")
    if survivors:
        lines += [f"{i}. {_fmt_obj(o)}" for i, o in enumerate(survivors)]
    else:
        lines.append("(none -- choice_index must be -1)")
    if rejected:
        lines.append("")
        lines.append("REJECTED BY THE FILTER (for context, not selectable):")
        lines += [f"- {_fmt_obj(o)} -- reasons: {', '.join(r)}"
                  for o, r in rejected]
    lines.append("")
    lines.append("A rewrite is allowed." if allow_rewrite
                 else "A rewrite is NOT allowed: rewrite must be null.")
    return "\n".join(lines)


def _add_usage(decision, result: dict) -> None:
    u = result.pop("_usage", None)
    if not isinstance(u, dict):
        return
    if not isinstance(decision.usage, dict):
        decision.usage = {}
    for k, v in u.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            decision.usage[k] = (decision.usage.get(k) or 0) + v


_INT_STR = re.compile(r"\s*(-?[0-9]+)\s*")


def _int_or_none(v):
    """An int index, or a numeric string like "1" / " 0 " (R25, matching
    R7's int() coercion of pick_index); None for bools, floats, and
    anything non-decimal."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        m = _INT_STR.fullmatch(v)
        if m:
            return int(m.group(1))
    return None


def finalize(decision, complete_json, history) -> str:
    """Choose the opener (if any) that goes out with a like; spec §3 0-6.

    Mutates `decision` and returns a status string. Never turns a like into
    a skip; every failure path ends in an empty comment. Model output is only
    written back after the full deterministic filter runs on it again.

    Statuses: "skip" (not a like), "premade", "chosen", "rewritten",
    "select_off" (OPENER_SELECT=False, stage-1 pick sent); empty comment:
    "empty_no_facts", "empty_no_candidates", "pick_invalid" (pick_index -1,
    or bad/filtered pick with OPENER_SELECT=False), "empty_all_rejected"
    (the filter left zero survivors and no acceptable rewrite came back),
    "empty_stage2_rejected" (stage 2 vetoed >=1 survivor, or the rewrite on
    rejudge), "stage2_error" (no backend, exception, unparseable output),
    "stage2_invalid_choice" (bad index, or chosen object fails re-check).

    Also records how it was chosen in `decision.opener_trace`: every
    stage-1 candidate with its filter verdict and whether it was stage 1's
    pick, each stage-2 call's choice / reason / rewrite (or error), the
    status, and the final text."""
    cands = list(decision.candidates or []) if decision.decision == "like" else []
    facts = [f for f in (decision.profile_facts or []) if isinstance(f, dict)]
    pick = _int_or_none(decision.pick_index)
    trace = {"candidates": [], "stage2": []}
    for i, c in enumerate(cands):
        c = c if isinstance(c, dict) else {}
        trace["candidates"].append({
            "text": c.get("text"), "fact_id": c.get("fact_id"),
            "form": c.get("form"), "stage1_pick": i == pick,
            "reject": check_opener(c, facts, history),
        })
    decision.opener_trace = trace
    status = _finalize(decision, complete_json, history, trace["stage2"])
    trace["status"] = status
    trace["final"] = decision.message if decision.decision == "like" else ""
    return status


def _finalize(decision, complete_json, history, stage2: list) -> str:
    """finalize's decision logic; appends each stage-2 call to `stage2`."""
    if decision.decision != "like":
        return "skip"

    # 0. Premade precedence.
    if decision.premade_id:
        p = judge_common.valid_premade(decision.premade_id)
        if p:
            decision.message = p["message"]
            decision.message_archetype = "premade"
            decision.anchor = None
            decision.prompt_referenced = ""
            if history is not None:
                history.add(decision.message)
            return "premade"
    decision.premade_id = ""

    facts = [f for f in (decision.profile_facts or []) if isinstance(f, dict)]
    cands = list(decision.candidates or [])
    pick = _int_or_none(decision.pick_index)

    # 1. Empty cases.
    _set_empty(decision)
    if not facts:
        return "empty_no_facts"
    if not cands:
        return "empty_no_candidates"
    if pick == -1:
        return "pick_invalid"

    # OPENER_SELECT = False: pick_index indexes the ORIGINAL candidates list,
    # resolved before any filtering, then validated directly. No stage 2.
    if not getattr(config, "OPENER_SELECT", True):
        if pick is None or not 0 <= pick < len(cands):
            return "pick_invalid"
        obj = cands[pick]
        if not _validate(obj, facts, history):
            return "pick_invalid"
        _write_back(decision, obj, facts, history)
        return "select_off"

    # 2. Filter; survivors renumbered 0..k-1, rejected shown without index.
    survivors, rejected = [], []
    for c in cands:
        reasons = check_opener(c, facts, history)
        if reasons:
            rejected.append((c if isinstance(c, dict) else {}, reasons))
        else:
            survivors.append(c)

    if complete_json is None:
        return "stage2_error"
    rewrites_left = max(0, int(getattr(config, "OPENER_RETRY", 1)))
    is_rewrite = False

    while True:
        allow_rewrite = rewrites_left > 0
        if not survivors and not allow_rewrite:
            return "empty_all_rejected"
        # 3. One text call.
        try:
            result = complete_json(
                SELECT_SYSTEM,
                _select_user(facts, survivors, rejected, allow_rewrite),
                SELECT_SCHEMA if allow_rewrite else _REJUDGE_SCHEMA,
                SELECT_NAME,
            )
        except Exception as e:  # noqa: BLE001 -- any backend failure
            print(f"[opener] stage 2 failed: {type(e).__name__}: {e}")
            stage2.append({"error": f"{type(e).__name__}: {e}"})
            return "stage2_error"
        if not isinstance(result, dict):
            stage2.append({"error": f"non-dict result: {result!r}"[:200]})
            return "stage2_error"
        _add_usage(decision, result)
        step = {k: result.get(k) for k in ("choice_index", "reason", "rewrite")}
        if isinstance(step["rewrite"], dict):
            step["rewrite_reject"] = check_opener(step["rewrite"], facts, history)
        stage2.append(step)
        if "choice_index" not in result:
            return "stage2_error"
        idx = _int_or_none(result.get("choice_index"))
        if idx is None:
            return "stage2_invalid_choice"

        if idx != -1:
            # 5a. choice_index indexes the survivor list.
            if not 0 <= idx < len(survivors):
                return "stage2_invalid_choice"
            obj = survivors[idx]
            # 5b. full filter again right before writeback.
            if not _validate(obj, facts, history):
                return "stage2_invalid_choice"
            _write_back(decision, obj, facts, history)
            return "rewritten" if is_rewrite else "chosen"

        # 4. Nothing chosen: a rewrite (if allowed) must pass §2, then is
        # re-judged once as the only survivor with rewrite disallowed. A
        # rewrite returned when disallowed is ignored.
        rewrite = result.get("rewrite") if allow_rewrite else None
        if not isinstance(rewrite, dict) or check_opener(rewrite, facts, history):
            # Stage 2 vetoed >=1 survivor (or the rewrite on rejudge) vs. the
            # filter having left nothing; a failing rewrite keeps its origin.
            return "empty_stage2_rejected" if survivors else "empty_all_rejected"
        rewrites_left = 0  # spec: rejudge disallows rewrite; <= 2 calls total
        survivors, rejected, is_rewrite = [rewrite], [], True
