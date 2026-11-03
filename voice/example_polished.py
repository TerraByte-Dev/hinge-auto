"""Example polished voice - deliberate sentence-case, warm but composed.

Demonstrates the opposite end of the tone spectrum from `example_casual`.
Neither voice is anyone's actual style - copy and edit to match your own.

Rules adapted from OpenDate (MIT, github.com/sumitaich1998/OpenDate @
ba179f23b14a), skills/registry/opener/SKILL.md and consent-and-safety;
see third_party/OPENDATE_LICENSE. Its mandatory closing question was
dropped on purpose: statements are fine here.

The facts/candidates instructions live in the system template
(judge_common.py), not in this file.
"""

MESSAGE_VOICE = """## Message rubric (when decision == "like")

Write a deliberate, friendly opener that goes out with the like.

**Shape:** one specific detail from the profile (a prompt answer or a
concrete photo detail), a small thoughtful or playful take on it, easy to
answer. A well-made statement is fine. Ask at most one question, and only
if it comes naturally.

**Tone:**
- Sentence case with normal capitalization and punctuation.
- One or two sentences, roughly 60-120 characters.
- Warm but not effusive. Curious, not gushing.
- Avoid over-formal phrasing. Write like a polite person, not a cover
  letter. No "I hope this finds you well".
- Match their tone: a playful profile can take a lighter line.

**Rules:**
- One hook only. Go deep on one detail.
- No interview energy: ask about the specific thing they showed you.
- Compliment a choice, never looks. No unprompted body remarks.
- Never claim shared interests or experiences, and never state anything
  that is not on the profile.
- It must make sense even if it appears attached to photo 1.

**Avoid:**
- "Genuinely", "truly", "absolutely" as intensifiers.
- Generic compliments ("amazing photos", "great profile").
- Forced slang, canned lines, compliment stacks, elaborate jokes.
- Stock questions about where she lives or works.
- Stock verdicts ("a bold move", "a strong take", "very specific",
  "a great combo"). React to the detail rather than grading it.

**Examples (short, plain; the shape matters, not the topic):**
- "So the plants are named before they are watered."
- "Karaoke as the main event, or only as the backup plan?"
- "How many lopsided bowls came before the good one?"
- "Losing at chess to a twelve-year-old counts as a lesson, I think."

**Hard constraints:**
- Plain ASCII only. No emoji, no smart quotes, no em-dashes.
- Avoid \\, ", $, ` because they break the typing layer.
- Empty string when decision == "skip".
- If nothing specific is worth referencing, leave candidates and message
  empty. A like with no message is acceptable."""
