# Opener generation — design

Date: 2026-11-01. Branch: `phone-galaxy-s23`. Status: approved in chat, pending spec review.

## Goal

Natural, profile-specific openers attached to a like (before matching) that
give the recipient an easy reason to reply. Matching the user's personal voice
is secondary. Replies to existing matches are out of scope.

Success means: every sent opener is grounded in one verified profile detail,
is short and casual, is not forced into a question, never invents shared
interests or experiences, is attached to the element it references (or reads
correctly when it falls back to photo 1), and the offline eval shows this on
varied fictional profiles. Eval scores are heuristics, not match-rate evidence.

## Constraints

- Free/open resources only; no paid dating-assistant products or new paid deps.
- Works with both live backends (`judge.py` Anthropic, `judge_ollama.py` Ollama)
  using existing model settings.
- Bounded cost: one extra text-only call per *like* (not per profile), at most
  one rewrite.
- Preserve screenshot-based extraction, existing config, `DRY_RUN = True`, and
  existing Decision fields consumed by `save_debug`, `runlog`, `metrics`.
- No likes/messages sent, no live account access, nothing published or
  committed without the user asking.

## Verified problems in the current checkout

1. `judge_common.DEFAULT_MESSAGE_VOICE` and `voice/example_casual.py` mandate
   "specific detail + short question"; the archetype enum centres
   `observation_question`. Result: repetitive structure.
2. `modes/example_lenient.py` (active mode) ships premade
   `"hey, your [prompt] caught my eye, what's the context"` with a literal
   placeholder; `enforce_premade_verbatim` would send it verbatim.
3. `main.do_like` always taps the topmost `Like photo` heart, so prompt- or
   photo-N-referencing comments land on photo 1. The phone's UI dump exposes
   `"Prompt: <q>. Answer: <a>"` followed by a `"Like prompt"` node, which makes
   prompt targeting by text feasible.

## Design

### 1. Stage 1 — vision call (existing call, extended schema)

`DECIDE_INPUT_SCHEMA` gains two fields, populated on likes (empty on skips):

- `profile_facts`: 0–8 items `{id, kind: photo|prompt|bio, index, prompt_text,
  answer_text, detail}` — only details clearly visible in the frames; fewer is
  correct for sparse or ambiguous profiles, never pad. `prompt_text`/
  `answer_text` are copied verbatim from the screenshot for prompts; `detail`
  is a short neutral description; `index` is the 1-based photo or prompt order.
- `candidates`: 0 to `config.OPENER_CANDIDATES` (default 3, max 4) items, each
  an **opener object** `{text, fact_id, form: question|observation|tease|
  either_or}`; `fact_id` must name an entry in `profile_facts`; forms should
  differ; at most one question-form candidate per profile about job/hometown.
- `pick_index`: integer index into `candidates` of the model's own pick, or
  -1 when there are no candidates.

**Opener object contract.** Every selectable output — each stage-1 candidate,
the stage-1 pick, and any stage-2 rewrite — is a `{text, fact_id, form}`
object. Nothing free-text is ever sent. The chosen object determines
`message` (= `text`), `message_archetype` (= `form`), `prompt_referenced`
(derived from the fact) and `anchor` (= the referenced fact). This holds in
every path, including `OPENER_SELECT = False`.

**Empty cases.** No facts, no candidates, or `pick_index == -1` on a like →
`message = ""`, `message_archetype = "empty"`, `anchor = None`; stage 2 is
not called. A like with no comment is a valid outcome.

Existing fields stay. The model still fills `message` (expected to equal the
picked candidate's text) for backward compatibility, but the pipeline
overwrites it from the chosen opener object; a stage-1 `message` without a
matching candidate is never sent. The archetype enum adds `observation`,
`either_or`, `question`; old values remain valid. `Decision` gains
`profile_facts: list`, `candidates: list` (default empty), `pick_index: int`
(default -1) and `anchor: dict | None` (default None). The new schema fields
are listed in `required` so the model always emits them (empty / -1 on skips),
but parsing tolerates their absence, so old debug/run artefacts and test mocks
still load.

### 2. Deterministic filter (free, `opener.py`)

Ported and adapted from OpenDate `src/opendate/orchestrator/quality.py`
(MIT, credited in the module docstring and `third_party/OPENDATE_LICENSE`):
its `_GENERIC`, `_CRINGE`, `_PRESSURE` regexes, `_STOPWORDS` /
`_content_words` grounding check, repetition check and ≥3-question
"interview energy" rule. Deliberately **not** ported: `SKILLS_EXPECTING_QUESTION`
(it penalises openers without a question — the habit this change removes) and
the persona/emoji and their-message length checks (no persona, no prior
message pre-match). Unlike OpenDate's scored critic, ours is a hard pass/fail
per rule with a reason, since stage 2 does the ranking.

Runs on each candidate before stage 2. Rejects (with a reason) when:

- non-ASCII, or contains `\ " $ \``, or empty;
- length > 140 chars or < 12 chars;
- OpenDate generic / cringe / pressure patterns (e.g. `tell me more`,
  `what do you do for fun`, `how's your day`, `from heaven`, `hey beautiful`,
  `you owe me`);
- unprompted sexual or body remarks (from OpenDate consent-and-safety:
  `sexy`, `hot body`, `body count`, `in bed`, etc.);
- banned phrases: AI tells (`genuinely`, `honestly`, `obsessed`, `iconic`,
  `underrated`, `fellow `, `the way you`, `something about`, `love that`),
  pickup-line patterns (`are you a`, `did it hurt`, `come here often`,
  `my place or`), invented-shared-experience patterns (`me too`, `i also`,
  `same here`, `we should`, `i love .* too`, `as a fellow`), appearance
  pile-ons (`gorgeous`, `beautiful`, `stunning`, `so hot`, `cute smile`);
- more than one `?`;
- grounding: no content word (stopword-filtered, crude stem) shared with its
  anchored fact's `answer_text`/`detail`; or `fact_id` doesn't exist;
- repetition: first three words match any of the last 20 sent/drafted openers
  (from the current run's in-memory history; the eval uses its own history).

### 3. Stage 2 — critique + select (text only, likes only)

`opener.finalize(decision, complete_json, history)` runs in `main` after the backend
returns, only when `decision.decision == "like"`. Order:

0. **Premade precedence.** If `decision.premade_id` names a valid premade
   (exists, no `[...]` placeholder, passes the typing-safety checks — ASCII and
   no `\ " $ \``), the premade wins: `message` = canonical premade text,
   `message_archetype = "premade"`, `anchor = None` (photo-1 target,
   `prompt_referenced = ""`), candidates ignored, stage 2 not called. Premades
   are user-authored, so banned-phrase/grounding rules don't apply to them.
   An invalid premade id or placeholder premade → `premade_id = ""` and the
   normal path below runs.
1. Empty cases (§1) → empty comment, stop.
2. Filter candidates (§2). Survivors are renumbered `0..k-1`; rejected
   candidates are shown to stage 2 only in a separate "rejected (for context,
   not selectable)" list with their reasons and no index.
3. One text call with the facts + survivors (+ the rejected list). The model
   scores survivors 1–5 on grounded, natural, easy_reply, specific; rejects any
   that invent shared interests or the sender's experiences, over-compliment,
   read as a line, or don't make sense if attached to photo 1; returns
   `{choice_index: int | -1, rewrite: {text, fact_id, form} | null, reason}`.
   No survivors → this call is made only to request a rewrite
   (`choice_index` must be -1).
4. If `choice_index == -1` and a rewrite is given: at most one rewrite
   (`config.OPENER_RETRY` above 1 has no effect, R9); a rewrite must pass §2 and is
   then re-judged once by the same stage-2 call (with the rewrite as the only
   survivor, `rewrite` disallowed). Still nothing → empty comment.
5. **Deterministic validation before writeback.** Whatever object is about to
   be written must (a) be resolved by its own path's index rule, and (b) pass
   the full §2 filter again, with its `fact_id` resolving to a fact:
   - stage-2 choice: `choice_index` indexes the **survivor list**
     (`0 <= i < k`);
   - rejudged rewrite: the rewrite object itself;
   - `OPENER_SELECT = False`: `pick_index` indexes the **original stage-1
     `candidates` list** (`0 <= pick_index < len(candidates)`), resolved to
     its object *before* any filtering; that object is then validated
     directly. Survivor renumbering is never applied in this mode.

   Failure → empty comment, logged as `stage2_invalid_choice` (or
   `pick_invalid` in disabled-selection mode). Model output never reaches
   writeback without this check.
6. Write back from the validated opener object (§1 contract); always clear
   `premade_id` on this path.

**Statuses** returned by `finalize` (logged per like, and the eval's empty
reasons): `skip`, `premade`, `chosen`, `rewritten`, `select_off`; empty
comment: `empty_no_facts`, `empty_no_candidates`, `pick_invalid`,
`empty_all_rejected` (the filter left zero survivors and no acceptable rewrite
came back), `empty_stage2_rejected` (stage 2 vetoed at least one survivor, or
the rewrite on rejudge), `stage2_error`, `stage2_invalid_choice`. A stage-2
`choice_index` given as a numeric string (`"1"`, `" 0 "`) is accepted like
`pick_index` (R7/R25); bools and non-decimal values are invalid.

**What is guaranteed where.** The semantic checks — invented shared
experience / sender biography, photo-1 compatibility, "reads like a line" —
exist only in stage 2. Therefore:

- Stage-2 call fails (exception, unparseable output, bad index) → **empty
  comment**; the like still goes out. No unchecked candidate is sent.
- `OPENER_SELECT = False` (opt-in, documented as weaker) → the stage-1
  `pick_index` candidate is sent only if it passes §2; otherwise empty. This
  mode does **not** guarantee the semantic checks, and the config comment says
  so.

Each backend exposes `complete_json(system: str, user: str, schema: dict,
name: str) -> dict`: Anthropic via a forced tool call with `config.MODEL`;
Ollama via `format=schema` with `config.OLLAMA_MODEL`. Stage 2 never turns a
like into a skip. Token usage is added to `decision.usage`. Existing backends
keep calling `enforce_premade_verbatim` before returning; `finalize` then
re-validates the premade per step 0, so the integration order is fixed and
tested.

### 4. Voice / rubric

`DEFAULT_MESSAGE_VOICE`, `voice/example_casual.py`, `voice/example_polished.py`
are rewritten around (adapted from OpenDate's MIT opener skill, credited):
one specific profile detail → a small playful or curious interpretation → an
easy reply. Statements are fine; at most one question; casual, concise; no
forced slang, canned lines, compliment stacks, or elaborate jokes; never claim
shared interests/experiences or details not on the profile; each opener must
make sense even if shown attached to photo 1. Examples are kept short and
plain. Voices keep their tonal differences (casual = lowercase, polished =
sentence case). Custom user voices keep working; the candidate/facts
instructions live in the system template, not the voice, so any voice gets
the pipeline.

Taken from OpenDate `skills/registry/opener/SKILL.md`: one hook only; 1–2
sentences; no interview energy; compliment a choice, not looks; match their
tone; answerable in five seconds. Not taken: its formula's mandatory closing
question and its three examples (each asks two questions, contradicting its
own "one is plenty" rule, and reads over-composed). From
`consent-and-safety/SKILL.md`: no false claims about the user, no unprompted
sexual/body remarks. From `persona-style-transfer/SKILL.md`: rewrites keep
meaning and never fabricate biography — these go into the stage-2 rewrite
instructions. No separate style-transfer call (voice is secondary; revisit
when the user writes their own voice).

Stage-2 reject reasons (§3) include the consent-and-safety honesty and
body-remark rules.

### 5. Premades

`modes/example_lenient.py` sets `PREMADES = []` with a comment showing the
schema. Prompt building omits, with a warning, any premade whose text contains
a `[...]` placeholder or fails typing safety, so the model never sees it;
`enforce_premade_verbatim` treats such ids as unknown. Precedence and metadata
cleanup are in §3 step 0. Tests cover a valid premade (sent verbatim, anchor
None, stage 2 not called), a placeholder premade (omitted from prompt, id
cleared, normal path), and an unknown id.

### 6. Targeting (`target.py`, `main.do_like`)

`do_like(message, identity, anchor=None)`; `anchor=None` keeps today's
targeting (photo 1).

**Constraint from the device.** The compose card has no close X, and Back
with no keyboard up leaves Hinge (repo CLAUDE.md). There is no verified way to
dismiss a compose card while staying on the same profile, and
`recover_from_failed_like()` skips the current profile. So the fallback
decision is made **before any heart is tapped**; after a heart tap there is no
retargeting.

**Identity is captured with the frames that were judged.** `capture_profile()`
returns `(frames, identity)`, where `ProfileIdentity` is recorded at the top
of the profile, at the moment `frames[0]` is taken:

- `skip_label`: the `Skip <name>` label from one UI dump (unreadable → `None`);
- `top_thumb`: a downscaled grayscale crop of `frames[0]` (same crop as
  `_profile_region_hash`) for near-match comparison;
- `top_hash`: `_profile_region_hash(frames[0])`.

The loop passes this object unchanged through `_attempt_like` into
`do_like`; it is never re-derived from the live screen.

**Verification before targeting.** After `scroll_back_to_top()` in `do_like`,
take a screenshot and a dump and require **both**:

1. the `Skip <name>` label equals `identity.skip_label` (and is not `None`);
2. the top-region thumbnail near-matches `identity.top_thumb` (mean absolute
   difference below a threshold fixed in config and calibrated in tests on
   identical, re-rendered and different frames).

The name alone is not trusted (names repeat); the image alone is not trusted
(near-match tolerance). Then, at every scroll step of prompt targeting and
immediately before any heart tap, the dump must still show the same
`Skip <name>` label (continuity on the verified profile).

Failure anywhere → `Halt("identity_mismatch")`: no tap, nothing typed, and no
recovery skip, since the screen is not the profile that was judged and
skipping would act on an unjudged profile. Consistent with the existing "unknown
state halts" rule.

- `kind == "prompt"`: from the top, up to N scroll steps, dump UI and find
  nodes starting `Prompt:`; normalise (lowercase, strip punctuation and
  trailing `.......` truncation) and score against the anchor's prompt/answer
  text (`difflib` ratio on question, plus answer-prefix containment); accept
  best ≥ 0.8. Take the nearest `Like prompt` node below it (same card: before
  the next `Prompt:`/photo node). If its y is in the lower part of the screen,
  nudge-scroll and re-dump to re-locate. Identity check, then tap; then the
  existing Send Like / comment / confirm flow.
- `kind` photo index 1, bio, unknown, or photo index > 1 → photo-1 path
  (photo-N label is identical for every photo, so counting is unreliable).
- **Pre-tap fallback** (prompt not matched within N steps, or match lost on
  re-dump): no heart has been tapped, so scroll back to top, identity check,
  and take the photo-1 path with the same comment. Nothing to dismiss.
- **Post-tap failure** (Send Like not found after the prompt-heart tap): no
  retarget. Raise `LikeAborted("like_aborted_detection")` exactly as today's
  photo-1 path does; the existing recovery skips this profile and nothing is
  sent. The comment is never carried to another screen.
- **Send confirmation compares like with like.** `_confirm_like_sent` takes a
  `ConfirmBaseline` instead of a single top hash:
  `{pre_tap_hash, pre_tap_viewport: "top" | "scrolled", identity,
  matched_prompt_label | None}`, where `pre_tap_hash` is taken from the screen
  at the heart tap (not the top). After Send, keeping the existing sheet
  handling and the "Send Like still visible" check, the like counts as
  confirmed only if the profile has **advanced**, decided on a **verified,
  normalised top view** so the scroll position can't fake it:
  1. *Quick rejects on the raw post-send screen:* region hash equals
     `pre_tap_hash`, or (prompt taps) a dump node equals
     `matched_prompt_label` → `LikeUnconfirmed`.
  2. *Normalise to the top and prove it is the top:* scroll up one swipe at a
     time, screenshotting after each, until two consecutive region hashes are
     identical (the view stopped moving). *As implemented (R22):* "stopped
     moving" is `identity.near_match` of consecutive top thumbnails, not md5
     equality, so a playing video or animated photo still settles. Bounded by the full
     `scroll_back_to_top` swipe count; never stabilising → `LikeUnconfirmed`.
     A freshly advanced profile is already at the top, so this normally costs
     one swipe and two screenshots; the same defensive top-scroll already
     runs on every fresh profile in `capture_profile()`.
  3. *Positive evidence on the verified top view:* the top thumbnail must
     **not** near-match `identity.top_thumb`. Near-match → the judged profile
     is still there → `LikeUnconfirmed`. Re-run the late-sheet check here too;
     that final dump must also show a `Skip <name>` control or the `Discover`
     label, otherwise `LikeUnconfirmed("post-send screen not Discover")` (R26).

  Only step 3 counts as evidence of advancing; steps 1–2 just reject early or
  make the comparison valid. The verified top view is kept as the new
  profile's starting state (the loop's next `capture_profile()` proceeds from
  it). Anything else → `LikeUnconfirmed` (halt), as today. The `Skip <name>` label
  is deliberately not used as positive evidence of advancing, since the next
  profile can share the name. The same three steps run for photo-1 taps
  (`pre_tap_viewport == "top"`); there, step 1's hash check is today's check.
- `runlog.event("target", ...)` records `prompt_matched` (score),
  `fallback_photo1` (reason), or `identity_mismatch`; `runlog last` summarises
  counts.
- Cost: one extra UI dump at capture time (about 1–2 s per profile, including
  skipped ones), plus the post-send top normalisation (normally one swipe and
  two screenshots per like). Accepted as the price of tying the action to the
  judged profile.
- Rose guard: only label-located `Like prompt`/`Like photo` nodes are
  tapped; sheet buttons still use `ui.safe_tap_label`. Right before the Send
  Like tap one fresh dump is taken and the Send point is refused if it lies on
  a Rose element (`ui.UnsafeTap` → halt); if that dump fails on the phone,
  `Halt("send_guard_unreadable")` with nothing sent (R24). The Send point is
  still found by vision, not by the "Send priority like with message" label.
- Unreadable name at capture (R23): a failed dump, or one with no
  `Skip <name>` label, is retried once after about 1 s, still before the first
  scroll. If the label is still missing on the phone, a like decision is not
  attempted: the profile is flagged `no_identity` and skipped (counted as a
  skip), instead of halting at verification. The emulator is unaffected (R5).

### 7. Offline eval (`evals/openers/`)

- `profiles.json`: ~24 fictional profiles written for this repo, tagged
  `sparse`, `ambiguous_image`, `serious`, `playful`, `mixed`. Photos are
  captions. Includes an `expected_absent` list per profile for hallucination
  checks where useful.
- `render.py`: Pillow renders each profile to Hinge-like PNG frames (photo
  boxes carrying the caption, prompt cards, info row), so stage 1 runs on
  images.
- `run.py --backend anthropic|ollama|claude-cli|codex-cli --pipeline
  legacy|new [--limit N] [--tags ...]` → `results/<ts>-<backend>-<pipeline>.jsonl`.
  `legacy` = frozen copy of today's system prompt/voice, single call.
  CLI backends live in `evals/openers/cli_backends.py`: `claude -p
  --output-format json` with frame paths and `--allowedTools Read`, or
  `codex exec -i <frame>...`; JSON extracted from text, one retry on parse
  failure. Never imported by the live loop. (Repo CLAUDE.md names a future
live `judge_claude_code.py`; these eval backends could be promoted to that
later, but not in this change.)
- The eval forces `decision = like` (fixed eval preferences "like every
  profile") so both pipelines face the same opener task on every profile.
- `report.py a.jsonl b.jsonl` → Markdown + HTML side by side.
  - **Coverage first:** nonempty-opener rate among likes, overall and per tag;
    empty-reason breakdown (no facts, no candidates, all filtered, stage-2
    rejected, stage-2 error); premade rate; rewrite rate; predicted target mix
    (prompt-anchored vs photo-1, i.e. how often the live fallback would apply).
  - **Quality, on nonempty openers only:** question-ending rate, median length,
    banned-phrase hits, grounding overlap with the source profile,
    opening-word repetition, hallucination flags (`expected_absent` hits,
    shared-experience / sender-biography patterns), anchor kind mix.
  - Optional `--judge` adds model ratings, labelled heuristic.
- **Acceptance targets** (heuristic, for this change only): nonempty coverage
  ≥ 85% on non-`sparse` profiles and ≥ 50% on `sparse`; new coverage no more
  than 10 points below legacy on any tag; zero `expected_absent` hits and zero
  filter-detectable violations among sent openers; question-ending rate below
  legacy's. Failing a target is reported, not hidden; the report states these
  are not match-rate evidence.
- Results dir gitignored except `.gitkeep`; report clearly states scores are
  simulated judgments.

### 8. Testing

New unit tests (no device, no network):

- filter rules, incl. a sender-experience opener ("I spent last summer hiking
  in Peru" against a hiking fact) that the filter alone may pass, asserting it
  is only sent when stage 2 approves it;
- schema/Decision backward compatibility (old dicts without new fields load);
- opener-object contract: every sent message has `fact_id`, `form` and
  `anchor`, in select, rewrite and `OPENER_SELECT=False` paths; stage-1
  `message` with no matching candidate is never sent;
- `finalize`: choose / rewrite-once-then-rejudge / give-up / stage-2 error →
  empty / empty facts / empty candidates / `pick_index=-1`, with a fake
  `complete_json`;
- premades: valid premade sent verbatim with anchor None and no stage-2 call;
  placeholder premade omitted from the prompt and id cleared; unknown id;
  `premade_id` cleared when a fresh candidate is chosen;
- stage-2 index safety: an in-range index that pointed at a rejected
  candidate in the original list cannot select it (survivor renumbering); an
  out-of-range or negative index other than -1 → empty; a chosen object that
  fails the re-run filter (e.g. a mocked survivor mutated to contain a banned
  phrase) → empty with `stage2_invalid_choice`;
- identity: capture returns identity from the frame-0 moment; changed profile
  before `do_like` (different name, different image) → `Halt`, no tap;
  same-name different profile (name matches, thumbnail differs) → `Halt`;
  name label changes mid prompt scan → `Halt`; unreadable name at capture →
  `Halt` at like time (no like without identity);
- confirmation (fake screens with a scroll-position model):
  - compose gone, same profile, still at the pre-tap position → rejected at
    step 1;
  - compose gone, same profile, **shifted to a third position** (≠ pre-tap,
    ≠ top, matched prompt off-screen) → passes step 1, normalised to the top
    in step 2, near-matches `top_thumb` in step 3 → `LikeUnconfirmed`;
  - compose gone, matched prompt label still present → `LikeUnconfirmed`;
  - top never stabilises within the swipe bound → `LikeUnconfirmed`;
  - genuine advance (new profile at top, same name allowed) → confirmed after
    one stabilising swipe;
  - photo-1 path: today's confirmed and unconfirmed cases still behave the
    same;
- `OPENER_SELECT = False`: candidate 0 rejected, `pick_index = 1` → candidate
  1 (original list) is validated and sent; `pick_index = 0` → empty
  (`pick_invalid`), and candidate 1 is **not** sent in its place;
- prompt text matching incl. truncation and near-miss rejection;
- `do_like` with fake screens: prompt matched → prompt heart tapped; pre-tap
  fallback → photo-1 heart on the same profile, no recovery skip called;
  post-tap Send Like missing → `LikeAborted`, no second heart tap; identity
  mismatch before tap → `Halt("identity_mismatch")`, nothing tapped or typed,
  no recovery skip;
- `render.py` frame output; report coverage and nonempty-only metrics
  (an all-empty run scores 0% coverage and fails acceptance).

Full existing suite must pass.

### 9. Config

New optional keys in `config.py`: `OPENER_CANDIDATES = 3`, `OPENER_RETRY = 1`,
`OPENER_SELECT = True` (False skips stage 2 and sends the stage-1 pick if it
passes the filter; the config comment states this loses the semantic checks).
No other defaults change.

## Third-party attribution

`third_party/OPENDATE_LICENSE` holds OpenDate's MIT license text and the
upstream URL + commit inspected (github.com/sumitaich1998/OpenDate @
`ba179f23b14a`). `opener.py` and the rewritten voice files
name the specific OpenDate files they adapt. Not used from OpenDate: skill
engine, orchestrator loop, LLM router, connectors, persona ingestion, and the
post-match skills.

## Out of scope

Replies to matches; fine-tuning; claims of improved match rate; integrating
FlirtCopilot/Hitch (optional manual benchmarks only); writing the user's own
mode/taste; photo-N targeting.

## Known risks

- Prompt targeting and the fallback are only fake-screen tested until the user
  runs a live like.
- A prompt-heart tap whose compose card can't be read costs that profile (it
  is skipped), by design, since no same-profile dismissal path is verified.
- The identity thumbnail threshold is calibrated on rendered/fixture frames,
  not live ones. If a profile's top region animates (video photo, loading
  shimmer), identity can falsely mismatch and halt the run. That fails safe
  (nothing sent) but may need a live threshold adjustment.
- Stage-2 errors produce comment-less likes; a flaky backend lowers coverage
  rather than quality. The report's empty-reason breakdown makes that visible.
- Stage-2 cost/latency (~+$0.005–0.01, +3–6s per like) is estimated, to be
  measured in the eval.
- Fictional rendered frames are cleaner than real screenshots; eval quality
  may overstate live quality.
- CLI-backend evals depend on the user's installed, signed-in CLI and its
  rate limits.
