"""Backend-agnostic pieces of the judging pipeline.

Both `judge.py` (Anthropic) and `judge_ollama.py` (Ollama) import from
here so the system prompt, decision shape, and tool schema stay in sync.

A backend module needs to expose `judge(frames: list[bytes]) -> Decision`.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import config


SYSTEM_PROMPT_TEMPLATE = """You are evaluating Hinge dating profiles on behalf of the user.

The user's preferences:
{preferences}
{age_clause}
You will be shown a sequence of screenshots representing a single profile, in
order from top to bottom. The profile may include photos, prompt responses
(short text), and basic info (age, height, location, job, education, etc.).

Decide LIKE or SKIP based on the user's preferences. Be reasonably selective —
don't like profiles that clearly don't match, but don't be unreasonably picky
either. When the profile is genuinely ambiguous, lean SKIP.

{message_voice}

{opener_pipeline}
{premades_section}
Submit your decision via the submit_decision tool."""


# Opener rules adapted from OpenDate (MIT, github.com/sumitaich1998/OpenDate
# @ ba179f23b14a), skills/registry/opener/SKILL.md: one hook only, 1-2
# sentences, no interview energy, compliment a choice not looks, match their
# tone, answerable in five seconds. NOT taken: its mandatory closing
# question and its examples. Also from consent-and-safety/SKILL.md (no false
# claims about the user, no unprompted body remarks). See third_party/.
#
# Generic, voice-neutral fallback used when the active mode does not set
# MESSAGE_VOICE. Replace by writing a voice file under voice/<name>.py
# and pointing your mode at it.
DEFAULT_MESSAGE_VOICE = """## Message rubric (when decision == "like")

Write a short opener that goes out with the like.

**Shape:** pick one specific detail from the profile (a prompt answer or a
concrete photo detail), add a small playful or curious take on it, and make
it easy to answer. A plain statement is fine. Ask at most one question, and
only when it comes naturally.

**Rules:**
- One hook only. Go deep on one detail, do not list several.
- One or two short sentences, roughly 40-110 characters.
- No interview energy: ask about the specific thing they showed you, never
  "what do you do for fun".
- Compliment a choice (their taste, their dog's name), never their looks.
  No unprompted body or appearance remarks.
- Match their tone: a goofy profile gets goofy, a dry one gets dry.
- Never claim shared interests or experiences, and never state anything
  that is not on the profile. Do not invent facts about yourself.
- It must make sense even if it appears attached to photo 1, so do not
  depend on seeing which photo or prompt it refers to.
- No forced slang, canned lines, stacked compliments or elaborate jokes.
- No stock verdicts on their detail ("is a bold/strong/solid move, take,
  policy or combo", "very specific", "great combo", "sounds like a
  perfect Sunday"). React to the detail itself instead of grading it.

**Examples (plain, short; the shape matters, not the topic):**
- "So the plants get a name before they get water."
- "Karaoke first or karaoke as the backup plan?"
- "How many lopsided bowls came before the good one?"
- "Losing at chess to a twelve-year-old counts as a lesson, I think."

**Hard constraints:**
- Plain ASCII only. No emoji, no smart quotes, no em-dashes.
- Avoid the characters \\, ", $, ` because they break the typing layer.
- Empty string when decision == "skip".
- If you would lean LIKE but cannot write a specific, non-generic opener,
  leave candidates empty and message empty. A like with no message is
  acceptable.

This is the GENERIC fallback voice. See voice/example_casual.py and
voice/example_polished.py for two contrasting starting points."""


_OPENER_PIPELINE = """## Facts and candidate openers (when decision == "like")

First list what is actually visible, then write openers grounded in it.

1. `profile_facts`: 0-8 concrete details clearly visible in the frames, with
   ids "f1", "f2", ... in order. Prompts: copy prompt_text and answer_text
   VERBATIM from the screenshot. Fewer facts is correct for sparse or
   ambiguous profiles. Never pad, never guess at unreadable text.
2. `candidates`: up to {candidate_count} candidate openers, each written in
   the voice above. Each references exactly ONE fact via `fact_id`. Give each
   a `form` (question, observation, tease or either_or) and make the forms
   differ. At most one candidate may be a question about their job or
   hometown. If no specific, non-generic opener exists, use an empty list.
3. `pick_index`: the index of your own best candidate, or -1 when there are
   none.
4. `message` should equal the picked candidate's text (it is replaced by the
   final selection anyway). Use an empty string when skipping.

When decision == "skip", `profile_facts` and `candidates` are empty lists."""


FACT_KINDS = ("photo", "prompt", "bio")
OPENER_FORMS = ("question", "observation", "tease", "either_or")


# JSON schema for the submit_decision tool. Backends wrap this in their
# own tool-spec envelope (Anthropic uses `input_schema`, Ollama uses
# OpenAI-compatible `parameters`).
DECIDE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "The profile's first name as shown at the top of the "
                "profile. Lowercase, ASCII letters only — strip "
                "spaces, punctuation, emoji. If not visible, use "
                "\"unknown\"."
            ),
        },
        "decision": {
            "type": "string",
            "enum": ["like", "skip"],
            "description": "Whether to like or skip this profile.",
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "How confident you are in the decision.",
        },
        "reasoning": {
            "type": "string",
            "description": (
                "One or two sentences explaining the decision. Reference "
                "specific details from the profile (e.g., a prompt answer, "
                "an activity in a photo, the bio/info line)."
            ),
        },
        "message": {
            "type": "string",
            "description": (
                "The opener to send with the like. Required when "
                "decision == \"like\"; use empty string when skipping. "
                "Max ~150 chars, plain ASCII, no emoji. See the message "
                "rubric in the system prompt."
            ),
        },
        "skip_reason": {
            "type": "string",
            "enum": ["none", "age", "preferences", "low_effort", "other"],
            "description": (
                "Categorical skip reason for downstream analytics. "
                "Use \"none\" when decision == \"like\". "
                "\"age\" when the AGE GATE clause triggered the skip. "
                "\"preferences\" when a specific PREFERENCES rule fired. "
                "\"low_effort\" when the profile was too thin to engage "
                "with (no readable prompts, single photo, etc.). "
                "\"other\" only if nothing else fits."
            ),
        },
        "message_archetype": {
            "type": "string",
            "enum": [
                "empty",
                "observation_question",
                "observation",
                "either_or",
                "question",
                "prompt_callback",
                "photo_callback",
                "tease",
                "premade",
                "other",
            ],
            "description": (
                "Categorical label for the message style. "
                "\"observation_question\" = specific detail + a question. "
                "\"observation\" / \"tease\" / \"either_or\" / \"question\" "
                "= the form of the chosen candidate opener. "
                "\"prompt_callback\" = references a prompt with no question. "
                "\"photo_callback\" = references a photo detail with no "
                "question. \"tease\" = mild playful disagreement. "
                "\"premade\" when the message is a verbatim copy of one of "
                "the mode's premade openers (set premade_id too). "
                "\"empty\" when the message is empty (skip OR a like with "
                "no opener). \"other\" only when nothing else fits."
            ),
        },
        "premade_id": {
            "type": "string",
            "description": (
                "Id of the premade opener used, when message_archetype == "
                "\"premade\". Must match one of the ids listed in the "
                "Premade openers section of the system prompt. Empty "
                "string when the message was written fresh or is empty."
            ),
        },
        "prompt_referenced": {
            "type": "string",
            "description": (
                "Short label (under 50 chars) of the prompt or photo "
                "detail the message references, e.g. \"travel prompt\", "
                "\"sunset photo 1\". Empty string when message is empty "
                "or when using a premade that doesn't reference a specific "
                "profile detail."
            ),
        },
        "profile_facts": {
            "type": "array",
            "maxItems": 8,
            "description": (
                "0-8 concrete details clearly visible in the frames, "
                "for likes (empty array when skipping). Give each an id "
                "\"f1\", \"f2\", ... in order. For prompts, copy "
                "prompt_text and answer_text VERBATIM from the screenshot. "
                "detail is a short neutral description. index is the "
                "1-based photo or prompt order. Fewer facts is correct "
                "for sparse or ambiguous profiles; never pad."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": list(FACT_KINDS)},
                    "index": {"type": "integer"},
                    "prompt_text": {"type": "string"},
                    "answer_text": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["id", "kind"],
            },
        },
        "candidates": {
            "type": "array",
            "maxItems": 4,
            "description": (
                "Candidate openers (empty array when skipping or when no "
                "specific, non-generic opener exists). Each has text, "
                "fact_id naming an id from profile_facts, and a form. "
                "Forms should differ between candidates. At most one "
                "question about job or hometown."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "fact_id": {"type": "string"},
                    "form": {"type": "string", "enum": list(OPENER_FORMS)},
                },
                "required": ["text", "fact_id", "form"],
            },
        },
        "pick_index": {
            "type": "integer",
            "description": (
                "Index into candidates of your pick, or -1 when there "
                "are no candidates."
            ),
        },
    },
    "required": [
        "name", "decision", "confidence", "reasoning", "message",
        "skip_reason", "message_archetype", "premade_id",
        "prompt_referenced", "profile_facts", "candidates", "pick_index",
    ],
}


@dataclass
class Decision:
    name: str
    decision: str  # "like" | "skip"
    confidence: str  # "low" | "medium" | "high"
    reasoning: str
    message: str = ""
    skip_reason: str = "none"
    message_archetype: str = "empty"
    premade_id: str = ""
    prompt_referenced: str = ""
    profile_facts: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    pick_index: int = -1
    anchor: dict | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    # Set by opener.finalize: candidates with filter verdicts, stage-2 calls,
    # status and the final text (openerlog records it).
    opener_trace: dict = field(default_factory=dict)


_DEFAULTS = {
    "name": "unknown",
    "decision": "skip",
    "confidence": "low",
    "reasoning": "",
    "message": "",
    "skip_reason": "other",
    "message_archetype": "empty",
    "premade_id": "",
    "prompt_referenced": "",
}


def _as_list(v: Any) -> list[dict]:
    """Accept a list, or a JSON string encoding one (open models do this).
    Anything else -> []."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, dict)]


def decision_from_dict(d: dict, usage: dict) -> Decision:
    """Build a Decision from a tool-call dict, tolerating schema drift:
    missing keys get defaults, unknown keys are dropped, enum-like fields
    are clamped, list fields may arrive JSON-encoded."""
    missing = [k for k in _DEFAULTS if k not in d]
    if missing:
        print(f"[judge] warning: response missing {', '.join(missing)}; "
              f"defaulted")
    merged = {**_DEFAULTS, **{k: v for k, v in d.items() if k in _DEFAULTS}}
    if merged["decision"] not in ("like", "skip"):
        merged["decision"] = "skip"
    if merged["confidence"] not in ("low", "medium", "high"):
        merged["confidence"] = "low"
    try:
        pick = int(d.get("pick_index", -1))
    except (TypeError, ValueError):
        pick = -1
    anchor = d.get("anchor")
    return Decision(
        **merged,
        profile_facts=_as_list(d.get("profile_facts")),
        candidates=_as_list(d.get("candidates")),
        pick_index=pick,
        anchor=anchor if isinstance(anchor, dict) else None,
        usage=usage,
    )


def resolve_voice(voice: str | None) -> str:
    """Resolve the active mode's MESSAGE_VOICE into a prompt string.

    Three shapes accepted:
      - None        -> use DEFAULT_MESSAGE_VOICE
      - "<name>"    -> single-token name; load voice/<name>.py and read
                       its MESSAGE_VOICE module attribute
      - "...\\n..." -> multi-line literal; passed through as-is
    """
    if voice is None:
        return DEFAULT_MESSAGE_VOICE
    stripped = voice.strip()
    is_name = (
        stripped
        and "\n" not in stripped
        and " " not in stripped
        and len(stripped) <= 64
        and all(c.isalnum() or c in "_-" for c in stripped)
    )
    if is_name:
        try:
            import importlib
            mod = importlib.import_module(f"voice.{stripped}")
        except ModuleNotFoundError as e:
            raise RuntimeError(
                f"MESSAGE_VOICE={stripped!r} but voice/{stripped}.py not found. "
                f"Create it (see voice/example_casual.py) or use a multi-line "
                f"string for MESSAGE_VOICE instead."
            ) from e
        msg = getattr(mod, "MESSAGE_VOICE", None)
        if not isinstance(msg, str) or not msg.strip():
            raise RuntimeError(
                f"voice/{stripped}.py must export a non-empty MESSAGE_VOICE string."
            )
        return msg
    return voice


_PLACEHOLDER = re.compile(r"\[[^\]]*\]")
_UNSAFE_CHARS = set('\\"$`')


def _premade_problem(p: dict) -> str | None:
    """Why a premade cannot be sent verbatim, or None when it is fine."""
    msg = p.get("message")
    if not isinstance(msg, str) or not msg.strip():
        return "empty message"
    if _PLACEHOLDER.search(msg):
        return "contains a [placeholder]"
    if not msg.isascii():
        return "not plain ASCII"
    if _UNSAFE_CHARS & set(msg):
        return "contains a character the typing layer cannot send"
    return None


def valid_premade(pid: str) -> dict | None:
    """The configured premade with this id, if it exists and is safe to send
    verbatim (no [placeholder], ASCII, no typing-unsafe characters)."""
    for p in config.PREMADES:
        if p.get("id") == pid:
            return p if _premade_problem(p) is None else None
    return None


def _premades_section(premades: list[dict]) -> str:
    kept = []
    for p in premades:
        problem = _premade_problem(p)
        if problem:
            print(f"[judge] warning: premade {p.get('id')!r} omitted "
                  f"({problem})")
        else:
            kept.append(p)
    premades = kept
    if not premades:
        return ""
    lines = [
        "",
        "## Premade openers (verbatim, mode-specific)",
        "",
        "Instead of writing a fresh opener, you may select one of the following "
        "pre-written messages. Each has explicit guidance for when to use it. "
        "Premades bypass the voice rules above — they are sent EXACTLY as "
        "written, including capitalization and punctuation. If you select a "
        "premade:",
        '  - set `premade_id` to its id',
        '  - set `message` to the premade\'s text VERBATIM (copy character-for-character)',
        '  - set `message_archetype` to "premade"',
        "",
        "If no premade fits the profile, write a fresh opener per the voice "
        "rules above and leave `premade_id` as an empty string.",
        "",
        "Available premades:",
        "",
    ]
    for i, p in enumerate(premades, 1):
        lines.append(f'{i}. id: "{p["id"]}"')
        lines.append(f'   message (verbatim): {p["message"]!r}')
        lines.append(f'   use_when: {p["use_when"]}')
        lines.append("")
    return "\n".join(lines)


def _age_clause(age_min: int | None, age_max: int | None) -> str:
    if age_min is None and age_max is None:
        return ""
    lo = age_min if age_min is not None else 18
    hi = age_max if age_max is not None else 99
    return (
        f"\nAGE GATE: only LIKE if the profile's stated age is between "
        f"{lo} and {hi} inclusive. Hinge shows age in basic-info "
        f"(\"NN\" next to height/location). If age is visible and out of "
        f"range, decision=skip, skip_reason=\"age\", message=\"\". If age "
        f"genuinely isn't visible across any frame, proceed with the normal "
        f"rubric.\n"
    )


def _opener_pipeline() -> str:
    n = max(1, min(int(getattr(config, "OPENER_CANDIDATES", 3)), 4))
    return _OPENER_PIPELINE.format(candidate_count=n)


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        preferences=config.PREFERENCES.strip(),
        age_clause=_age_clause(config.AGE_MIN, config.AGE_MAX),
        message_voice=resolve_voice(config.MESSAGE_VOICE).strip(),
        opener_pipeline=_opener_pipeline(),
        premades_section=_premades_section(config.PREMADES),
    )


def enforce_premade_verbatim(decision: Decision) -> None:
    """If the model picked a valid premade by id, overwrite `message` with
    the canonical text so character drift can't leak. Unknown or unsafe ids
    (e.g. a premade with a [placeholder]) are cleared."""
    if not decision.premade_id:
        return
    p = valid_premade(decision.premade_id)
    if p:
        decision.message = p["message"]
        decision.message_archetype = "premade"
        return
    print(
        f"[judge] warning: unknown or unusable premade_id="
        f"{decision.premade_id!r}, clearing and treating message as fresh"
    )
    decision.premade_id = ""
    if decision.message_archetype == "premade":
        decision.message_archetype = "other"


def load_backend():
    """Resolve config.JUDGE_BACKEND to a module exposing judge(frames)."""
    backend = getattr(config, "JUDGE_BACKEND", "anthropic").lower()
    if backend == "anthropic":
        import judge
        return judge
    if backend == "ollama":
        import judge_ollama
        return judge_ollama
    raise ValueError(
        f"Unknown JUDGE_BACKEND={backend!r}. Use 'anthropic' or 'ollama'."
    )
