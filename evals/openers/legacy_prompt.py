"""FROZEN snapshot of the pre-opener-generation judging prompt.

Source: git commit f0d45c4 (judge_common.py SYSTEM_PROMPT_TEMPLATE,
DEFAULT_MESSAGE_VOICE, DECIDE_INPUT_SCHEMA, _premades_section, and
modes/example_lenient.py PREMADES). Do NOT import judge_common here and do
not edit the text: the offline eval uses it as the "before" arm.

SYSTEM_PROMPT is pre-formatted with preferences "Decision: like every
profile.", no age clause, the old default voice, and the old premade
section containing the example_lenient placeholder premade.
"""

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
{premades_section}
Submit your decision via the submit_decision tool."""

DEFAULT_MESSAGE_VOICE = """## Message rubric (when decision == "like")

Write a short opener that goes out with the like.

Aim for: one specific reference to something visible in the profile (a
prompt answer or a concrete photo detail), followed by a short question
about it. Keep it friendly and curious. Around 60-120 characters total.

Constraints:
- Plain ASCII only. No emoji, no smart quotes, no em-dashes.
- Avoid the characters \\, ", $, ` — they break the typing layer.
- Empty string when decision == "skip".
- If you'd lean LIKE but cannot write a specific, non-generic opener,
  output the empty string for `message` and set `message_archetype` to
  "empty". A like with no message is acceptable.

This is the GENERIC fallback voice. Most users will want to override it
by setting `MESSAGE_VOICE` in their mode file (or pointing it at a
template under voice/). See voice/example_casual.py and
voice/example_polished.py for two contrasting starting points."""

SCHEMA = {
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
                "prompt_callback",
                "photo_callback",
                "tease",
                "premade",
                "other",
            ],
            "description": (
                "Categorical label for the message style. "
                "\"observation_question\" = specific detail + a question. "
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
    },
    "required": [
        "name", "decision", "confidence", "reasoning", "message",
        "skip_reason", "message_archetype", "premade_id",
        "prompt_referenced",
    ],
}

PREMADES = [
    {
        "id": "prompt_curious",
        "message": "hey, your [prompt] caught my eye, what's the context",
        "use_when": (
            "Example placeholder showing the premade schema. Replace this "
            "with your own opener text, and update `use_when` to describe "
            "the situations where the judge should pick it. The judge "
            "literally pastes `message` into the comment field, so write "
            "it as a complete sentence you'd send."
        ),
    },
]

def _premades_section(premades: list[dict]) -> str:
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


PREFERENCES = "Decision: like every profile."

SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(
    preferences=PREFERENCES,
    age_clause="",
    message_voice=DEFAULT_MESSAGE_VOICE.strip(),
    premades_section=_premades_section(PREMADES),
)
