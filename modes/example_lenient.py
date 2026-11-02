"""Example mode — generous "default LIKE" rubric.

Demonstrates the mode-file format with a forgiving baseline: like almost
everyone, skip only on clear bot / spam / empty-profile signals. Copy this
to a new file and adapt the PREFERENCES string for your own taste.

Paired with `example_strict.py` (its antonym) so you can see the dynamic
range of what these prompts can do. Neither one represents anyone's
actual preferences — they exist as scaffolding.
"""

NAME = "example_lenient"
DESCRIPTION = "Generous baseline: default LIKE, skip only on bot/spam/empty tells."

# Hinge's in-app age filter is the right place to set an age band. Leaving
# these as None means the judge doesn't enforce an age gate.
AGE_MIN = None
AGE_MAX = None

# None = inherit the generic fallback in judge.DEFAULT_MESSAGE_VOICE.
# To use a packaged voice, set e.g. MESSAGE_VOICE = "example_casual" and
# the loader resolves it from the voice/ directory.
MESSAGE_VOICE = None

MAX_LIKES_PER_SESSION = None
MAX_PROFILES_PER_SESSION = None

# Optional verbatim openers the judge may pick instead of a generated one.
# They bypass the voice rules - the text goes out exactly as written - so
# they must be complete, plain-ASCII messages with no [placeholder] and none
# of the characters \ " $ ` (premades breaking these rules are dropped with
# a warning). Leave empty to disable. Schema:
#
# PREMADES = [
#     {
#         "id": "short_id",
#         "message": "a complete message you would send as-is",
#         "use_when": "the situations where the judge should pick it",
#     },
# ]
PREMADES = []

PREFERENCES = """
Default decision: LIKE.

This is the lenient example. Be generous — a missed like costs much less
than a missed match. The vast majority of profiles should be liked.

Skip ONLY when one of these clearly applies:

1. The profile looks fake / bot-like / spam. Telltale signs: only one
   photo and it looks AI-generated or stock; bio is a single off-platform
   handle ("snap me at X", "find me on TikTok @Y"); the photos and the
   stated info contradict each other in obvious ways.

2. The profile is genuinely empty — no readable prompt answers, no
   meaningful bio, photos are all unrecognizable (e.g. only blurry group
   shots or pets, no person visible).

3. Explicit deal-breaker statements that put you well outside the
   intended audience. For most users this rule should fire rarely; do
   not invent deal-breakers that aren't actually stated on the profile.

Everything else is a LIKE. Specifically:

- Career, hobbies, vibe, photo quality, group dynamics, distance, height,
  whether they drink, what they post about — none of these are skip
  signals in this lenient mode.

- If the profile is OK but you can't think of a strong, specific opener,
  output decision=like with message="" and message_archetype="empty".
  A like with no message is better than a skip.

When writing the reasoning field:
- Reference concrete details ("photo 3 shows...", "the [prompt] answer
  about X").
- Stay neutral and non-judgmental about people. The point of this
  example is to demonstrate the FORMAT, not to take a position on who
  is or isn't worth liking. Adapt the PREFERENCES text above to your
  own taste in your own mode file.
"""
