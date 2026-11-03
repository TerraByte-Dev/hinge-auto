"""Example casual voice - low-key, lowercase, conversational.

Demonstrates one end of the tone spectrum. Pair with `example_polished`
to see the contrast. Neither voice is anyone's actual style - copy and
edit to match your own.

Rules adapted from OpenDate (MIT, github.com/sumitaich1998/OpenDate @
ba179f23b14a), skills/registry/opener/SKILL.md and consent-and-safety;
see third_party/OPENDATE_LICENSE. Its mandatory closing question was
dropped on purpose: statements are fine here.

The facts/candidates instructions live in the system template
(judge_common.py), not in this file.
"""

MESSAGE_VOICE = """## Message rubric (when decision == "like")

Write a short, casual opener that goes out with the like.

**Shape:** one specific detail from the profile (a prompt answer or a
concrete photo detail), a small playful or curious take on it, easy to
answer. A plain statement is fine. Ask at most one question, and only if
it comes naturally.

**Tone:**
- All lowercase. No exclamation marks.
- One or two short sentences, roughly 40-90 characters.
- Sound like a normal person texting, not a customer-service reply.
- Pick curiosity or mild playfulness over agreement or compliments.
- Match their energy: goofy profile, goofy opener; dry profile, dry opener.

**Rules:**
- One hook only. Go deep on one detail.
- No interview energy: ask about the specific thing they showed you.
- Compliment a choice, never looks. No unprompted body remarks.
- Never claim shared interests or experiences, and never state anything
  that is not on the profile.
- It must make sense even if it appears attached to photo 1.

**Avoid (instant AI / generic-opener tells):**
- "genuinely", "honestly", "truly" as intensifiers
- "love that", "obsessed", "iconic", "underrated", "fellow [X]"
- "the way you...", "something about..."
- Em-dashes. Plain hyphens and periods only.
- Forced slang, canned lines, compliment stacks, elaborate jokes.
- Stock questions about where she lives, works, or what she does.
- Stock verdicts: "is a bold/strong/solid move", "very specific",
  "great combo", "sounds like a perfect sunday". React, don't grade.

**Examples (short, plain; copy the shape, not the topic):**
- "so the plants get names before they get water"
- "karaoke first or karaoke as the backup plan"
- "how many lopsided bowls came before the good one"
- "losing at chess to a twelve year old counts as a lesson i think"

**Hard constraints:**
- Plain ASCII only. No emoji, no smart quotes, no em-dashes.
- Avoid \\, ", $, ` because they break the typing layer.
- Empty string when decision == "skip".
- If nothing specific is worth referencing, leave candidates and message
  empty. A like with no message is acceptable."""
