# Opener Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grounded, natural pre-match openers chosen from a few candidates by a bounded two-stage pipeline, attached to the right prompt when possible, verified against the judged profile, and measured by an offline eval.

**Architecture:** The existing vision call (`judge.py` / `judge_ollama.py`) now also returns verified facts, opener candidates and a pick. A new `opener.py` runs a free deterministic filter, then one text-only critique/select call via each backend's new `complete_json`, with at most one rewrite. `identity.py` ties the like action to the profile that was judged. `target.py` finds the referenced prompt in the UI dump before any heart is tapped. `evals/openers/` renders fictional profiles to PNGs and compares legacy and new pipelines.

**Tech Stack:** Python 3.12 (`.venv`), anthropic, Pillow, numpy, stdlib `difflib`/`subprocess`; optional ollama; pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-opener-generation-design.md`. Read it with this plan; section refs (§n) point there.

## Global Constraints

- Repo rule overrides the skill default: **no commits** unless the user asks. Each task ends with a full-suite checkpoint instead of a commit.
- `DRY_RUN = True` stays. Never run `main.py` live, send likes or messages, touch the phone, or publish.
- No new required dependencies. `ollama` stays optional; CLI eval backends need a user-installed `claude` or `codex` binary and are imported only by `evals/`.
- Never print `.env` or key values; redact `sk-ant-...` in any output.
- Plain ASCII openers; never contain `\ " $ \``; length 12–140 chars; at most one `?`.
- New config keys only: `OPENER_CANDIDATES = 3` (max 4), `OPENER_RETRY = 1`, `OPENER_SELECT = True`, `IDENTITY = {"thumb_size": (64, 128), "thumb_max_diff": 6.0}`, `TARGET = {"prompt_min_score": 0.8, "max_scroll_steps": 7, "low_y_frac": 0.55}`. No other default changes.
- Old `Decision` dicts (no new fields) must still load; old archetype values stay valid.
- OpenDate code/rules adapted with credit: github.com/sumitaich1998/OpenDate @ `ba179f23b14a`, MIT.
- Test command: `.venv/bin/python -m pytest tests -q` (run from `~/dating/hinge-auto`). The existing suite must stay green after every task.

## Review Focus

1. **Emulator device profile** (`config.DEVICE == "emulator"`): uiautomator labels may be unavailable, so a `None` `skip_label` would halt every like. Expected: on the emulator, identity verification uses the thumbnail only and prompt targeting is skipped (photo 1), matching `recover_from_failed_like`'s existing DEVICE branch. Test in Task 5.
2. **Model returns `fact_id`s as ints or with whitespace / case drift** (`" F1"`, `1`). Expected: normalised to the fact's id string before lookup, not rejected. Test in Task 2.
3. **Prompt text with apostrophes and curly quotes**, e.g. UI `"I’m"` vs model `"I'm"`. Expected: the matcher normalises both to the same form. Test in Task 6.
4. **Repetition history across a run:** only *sent* openers enter history, not rejected drafts, so one bad profile doesn't block good phrasing later. Test in Task 3.
5. **Ollama returning `profile_facts`/`candidates` as JSON strings** instead of lists. Expected: parsed when valid JSON, otherwise treated as empty (comment-less like), no crash. Test in Task 1.

---

### Task 1: Decision schema extension and a shared tolerant parser

**Files:**
- Modify: `judge_common.py` (schema, `Decision`, new `decision_from_dict`)
- Modify: `judge.py:73-80` (use `decision_from_dict`)
- Modify: `judge_ollama.py:74-98` (`_decision_from_args` delegates to `decision_from_dict`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `Decision` fields `profile_facts: list[dict] = []`, `candidates: list[dict] = []`, `pick_index: int = -1`, `anchor: dict | None = None`. `decision_from_dict(d: dict, usage: dict) -> Decision`. `FACT_KINDS = ("photo", "prompt", "bio")`, `OPENER_FORMS = ("question", "observation", "tease", "either_or")`.

- [ ] **Step 1: Write failing tests** in `tests/test_schema.py`:
  - `test_old_dict_loads`: a dict with only the nine legacy keys → `profile_facts == []`, `candidates == []`, `pick_index == -1`, `anchor is None`.
  - `test_unknown_keys_dropped`: an extra key `"foo"` doesn't raise.
  - `test_string_encoded_lists_parsed`: `candidates='[{"text":"a","fact_id":"f1","form":"observation"}]'` → a list of length 1; `candidates="not json"` → `[]` (Review Focus 5).
  - `test_schema_has_new_required_fields`: `"profile_facts"`, `"candidates"`, `"pick_index"` are in `DECIDE_INPUT_SCHEMA["required"]`; the archetype enum contains `"observation"`, `"either_or"`, `"question"` and still `"observation_question"`.
  - `test_bad_enums_clamped`: `decision="maybe"` → `"skip"`; `confidence="x"` → `"low"`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_schema.py -q`. Expected: FAIL (ImportError on `decision_from_dict`).
- [ ] **Step 3: Implement.** Extend the schema per spec §1. Facts are an array of objects with `id` (string), `kind` (enum `FACT_KINDS`), `index` (int), `prompt_text`, `answer_text`, `detail` (strings), `maxItems: 8`. Candidates have `maxItems: 4`, items `{text, fact_id, form}` with `form` enum `OPENER_FORMS`. `pick_index` is an integer. Description strings carry §1's rules: verbatim prompt text, "fewer facts is correct, never pad", `-1` when no candidates. Move the defaults, clamping and drop-unknown-keys logic from `judge_ollama._decision_from_args` into `decision_from_dict`, plus JSON-string-to-list coercion. `judge.py` calls `decision_from_dict(block.input, usage)`.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: all PASS.

### Task 2: Deterministic opener filter

**Files:**
- Create: `opener.py` (filter part)
- Create: `third_party/OPENDATE_LICENSE` (OpenDate MIT text, URL, commit `ba179f23b14a`, list of adapted files)
- Test: `tests/test_opener_filter.py`

**Interfaces:**
- Produces: `check_opener(obj: dict, facts: list[dict], history: "OpenerHistory") -> list[str]` (empty list means pass; otherwise reason codes). `resolve_fact(fact_id, facts) -> dict | None`. `class OpenerHistory(maxlen=20)` with `add(text: str)` and `starts_like(text: str) -> bool` (first three lowercased words match an entry). Reason codes: `"empty"`, `"non_ascii"`, `"unsafe_char"`, `"too_long"`, `"too_short"`, `"generic"`, `"cringe"`, `"pressure"`, `"body_remark"`, `"ai_tell"`, `"pickup_line"`, `"shared_experience"`, `"appearance"`, `"multi_question"`, `"bad_fact"`, `"bad_form"`, `"ungrounded"`, `"repeat"`.

- [ ] **Step 1: Write failing tests.** Use a fixture fact list `[{"id":"f1","kind":"prompt","index":1,"prompt_text":"Typical Sunday","answer_text":"run if my legs work, market if they don't, nap either way","detail":"sunday routine"}]`. One parametrised test per reason code, using an example string from spec §2 for each. Plus:
  - `test_clean_statement_passes`: `"the nap being non-negotiable either way is the right call"` → `[]`. No question mark needed.
  - `test_three_questions_rejected`: reports `multi_question`.
  - `test_fact_id_drift_normalised`: `fact_id=" F1 "` resolves `f1`; integer `1` resolves the fact at position 1 when ids are `"f1".."fN"` (Review Focus 2).
  - `test_sender_experience_can_pass_filter`: `"I spent last summer hiking in Peru"` against a hiking fact returns `[]` from the filter alone. This documents that only stage 2 catches it; Task 3 asserts it isn't sent without stage-2 approval.
  - `test_history_repeat`: after `add("ok the nap thing")`, `"ok the nap again"` → `repeat`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_opener_filter.py -q`. Expected: FAIL (no module `opener`).
- [ ] **Step 3: Implement** in `opener.py`. Module docstring credits OpenDate `orchestrator/quality.py`. Port its `_GENERIC`, `_CRINGE`, `_PRESSURE` regexes and `_STOPWORDS` / `_content_words` verbatim (with attribution comment), plus our pattern sets from spec §2. Grounding: the candidate's content words, crudely stemmed (strip a trailing `s`, `es`, `ing`, `ed`), must intersect those of the fact's `answer_text + detail + prompt_text`. Do **not** port `SKILLS_EXPECTING_QUESTION`.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 3: Stage-2 finalize and the backends' `complete_json`

**Files:**
- Modify: `opener.py` (add `finalize`, `SELECT_SCHEMA`, `SELECT_SYSTEM`)
- Modify: `judge.py` (add `complete_json`), `judge_ollama.py` (add `complete_json`)
- Modify: `config.py` (add `OPENER_CANDIDATES`, `OPENER_RETRY`, `OPENER_SELECT` near the `MODEL` block; the comment says `OPENER_SELECT=False` loses the semantic checks)
- Test: `tests/test_opener_finalize.py`

**Interfaces:**
- Consumes: Task 1 `Decision`; Task 2 `check_opener`, `resolve_fact`, `OpenerHistory`.
- Produces: `finalize(decision: Decision, complete_json: Callable[[str, str, dict, str], dict] | None, history: OpenerHistory) -> str`, which mutates the decision and returns a status: `"premade"`, `"chosen"`, `"rewritten"`, `"empty_no_facts"`, `"empty_no_candidates"`, `"empty_all_rejected"`, `"stage2_error"`, `"stage2_invalid_choice"`, `"pick_invalid"`, `"select_off"`, or `"skip"`. Backends get `complete_json(system: str, user: str, schema: dict, name: str) -> dict`. Anthropic forces a tool call named `name` with `config.MODEL`, `max_tokens=800`. Ollama passes `format=schema` to `client.chat` with `config.OLLAMA_MODEL` and `json.loads` the content. Both raise on failure; `finalize` catches.

- [ ] **Step 1: Write failing tests** with a `FakeSelect` recording calls and returning scripted dicts:
  - `test_skip_untouched`: on a skip decision, returns `"skip"` and makes no call.
  - `test_empty_facts` / `test_empty_candidates` / `test_pick_minus_one_select_off`: return the `empty_*` / `pick_invalid` statuses; `message == ""`, `message_archetype == "empty"`, `anchor is None`; zero calls.
  - `test_chosen_writes_contract`: survivor chosen → `message == text`, `message_archetype == form`, `anchor == fact`, `prompt_referenced` non-empty, `premade_id == ""`.
  - `test_survivor_renumbering`: candidate 0 rejected (banned phrase) and candidate 1 clean. The user prompt the fake receives lists candidate 1 as index 0, and candidate 0 has no index. `choice_index=0` sends candidate 1. `choice_index=1` → `stage2_invalid_choice`, empty.
  - `test_chosen_revalidated`: the fake returns `choice_index=0` and the test mutates the survivor's text to a banned phrase before writeback (via a hook or a patched `check_opener` second call) → `stage2_invalid_choice`.
  - `test_rewrite_once_then_rejudge`: first call `{-1, rewrite}`, second call (rewrite as sole survivor) `{0, null}` → `"rewritten"`, exactly 2 calls. With `OPENER_RETRY=1`, a second rewrite request → empty, no third call.
  - `test_stage2_exception_empty`: the fake raises → `"stage2_error"`, empty, the like is not turned into a skip.
  - `test_sender_experience_needs_stage2`: the Peru opener is the only candidate and stage 2 rejects it (`{-1, null}`) → empty. With `complete_json=None` and `OPENER_SELECT=True` → `stage2_error`, empty.
  - `test_select_off_uses_original_index`: `OPENER_SELECT=False`, candidate 0 rejected, `pick_index=1` → candidate 1 sent (`"select_off"`). `pick_index=0` → `pick_invalid`, and candidate 1 is not sent.
  - `test_history_only_sent`: after a run where stage 2 rejected everything, history is empty; after a send, history contains the sent text (Review Focus 4).
  - `test_usage_accumulates`: the fake returns a `_usage` key → added to `decision.usage`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_opener_finalize.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `finalize` following spec §3 steps 0–6 exactly. Premade handling (step 0) calls Task 4's `judge_common.valid_premade(pid)`. Until Task 4 lands, stub it as "no premades". `SELECT_SYSTEM` holds the stage-2 rubric: score survivors 1–5 on grounded / natural / easy_reply / specific; reject invented shared interests or sender biography, compliment stacks, lines, and anything that doesn't read sensibly if attached to photo 1; rewrites keep meaning and never fabricate biography (OpenDate consent-and-safety and persona-style-transfer, credited). `SELECT_SCHEMA = {choice_index: int, rewrite: {text, fact_id, form} | null, reason: str}`.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 4: Voice rewrite, system template and premade sanitation

**Files:**
- Modify: `judge_common.py` (`SYSTEM_PROMPT_TEMPLATE` gains a facts/candidates section that is independent of the voice; rewrite `DEFAULT_MESSAGE_VOICE`; `_premades_section` filters; add `valid_premade`; `enforce_premade_verbatim` uses it)
- Modify: `voice/example_casual.py`, `voice/example_polished.py`
- Modify: `modes/example_lenient.py` (`PREMADES = []` plus a commented schema example)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Produces: `valid_premade(pid: str) -> dict | None` (exists in `config.PREMADES`, no `\[[^\]]*\]` placeholder, ASCII, no unsafe chars).

- [ ] **Step 1: Write failing tests:**
  - `test_prompt_has_no_question_mandate`: `build_system_prompt()` with `MESSAGE_VOICE=None`, `"example_casual"` or `"example_polished"` contains neither `"followed by a short question"` nor `"then a short question"`, and contains `"at most one question"` and `"attached to photo 1"`.
  - `test_candidates_section_with_custom_voice`: a literal multi-line custom voice still yields a prompt containing the `profile_facts` / `candidates` instructions.
  - `test_placeholder_premade_omitted`: `PREMADES=[{"id":"p","message":"hey [prompt]","use_when":"x"}]` → `"hey [prompt]"` is not in the prompt, `valid_premade("p") is None`, and `enforce_premade_verbatim` clears the id.
  - `test_valid_premade_verbatim`: a valid premade appears in the prompt. A decision with that id gets the canonical text. Then `opener.finalize` → `"premade"`, `anchor is None`, `prompt_referenced == ""`, no select call.
  - `test_fresh_choice_clears_premade_id`: a decision with a bogus `premade_id` plus candidates → after `finalize`, `premade_id == ""`.
  - `test_example_lenient_has_no_placeholders`: `modes.load("example_lenient").PREMADES == []`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_prompt.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Write the voice text per spec §4, credited to OpenDate `skills/registry/opener/SKILL.md` in a comment. Give 3–4 short plain examples per voice, mixing statements and single questions, none longer than 100 chars, none claiming shared experience. Casual stays lowercase; polished stays sentence case. Remove the Task 3 stub.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 5: Profile identity, loop integration and normalised confirmation

**Files:**
- Create: `identity.py`
- Modify: `config.py` (`IDENTITY` dict)
- Modify: `main.py`: `capture_profile` returns `(frames, identity)`; the loop calls `finalize_opener(decision)` after judging and before logging/`save_debug`; `_attempt_like(n, decision, frame0, identity)`; `do_like(message, identity, anchor=None)` verifies identity after scroll-back; `_confirm_like_sent(baseline)` runs spec §6's three steps; new `scroll_to_verified_top() -> bytes | None`.
- Modify: `tests/test_loop.py`, `tests/test_do_like.py`, `tests/test_preflight.py:83`, `tests/test_locations.py:66` (mock signatures; patch `main.finalize_opener` to a pass-through in loop tests)
- Test: `tests/test_identity.py`, additions to `tests/test_do_like.py`

**Interfaces:**
- Consumes: Task 3 `opener.finalize`, `OpenerHistory`.
- Produces:
  - `@dataclass(frozen=True) class ProfileIdentity: skip_label: str | None; top_thumb: bytes; top_hash: str`
  - `identity.capture(frame0: bytes, nodes: list[ui.UiNode] | None) -> ProfileIdentity`
  - `identity.thumb(png: bytes) -> bytes` (the `_profile_region_hash` crop, grayscale, resized to `IDENTITY["thumb_size"]`)
  - `identity.near_match(a: bytes, b: bytes) -> bool` (mean abs diff ≤ `thumb_max_diff`)
  - `identity.skip_label(nodes) -> str | None` (first label starting `"Skip "`)
  - `identity.verify(ident, png, nodes) -> str | None` (`None` means ok; otherwise a reason)
  - `@dataclass class ConfirmBaseline: pre_tap_hash: str; pre_tap_viewport: str; identity: ProfileIdentity; matched_prompt_label: str | None`
  - `main.finalize_opener(decision) -> str` wraps `opener.finalize(decision, getattr(_backend, "complete_json", None), _history)`.
  - `identity.region_crop(img) -> Image` holds the crop now used by `_profile_region_hash`. `main._profile_region_hash` stays in `main` and calls it, because existing tests monkeypatch `main._profile_region_hash` and that has to keep working. `identity.py` must not import `main`, to avoid an import cycle.

- [ ] **Step 1: Write failing tests.**
  `tests/test_identity.py`, using Pillow-generated PNGs:
  - identical frames near-match;
  - the same frame with a different status-bar clock near-matches;
  - two different rendered profiles don't;
  - `verify` returns `"name_mismatch"`, `"thumb_mismatch"` or `"no_label"` for those cases;
  - on `DEVICE="emulator"`, a `None` label passes when the thumbnail matches (Review Focus 1).

  `tests/test_do_like.py`: extend `FakePhone` with a scroll-position model. Screens are keyed by `(profile, position)`; `scroll_up`/`scroll_down` move the position; the top is position 0, and scrolling up there is a no-op, so the hash stabilises. Add:
  - `test_identity_changed_profile_halts`: the profile on screen ≠ the judged profile → `main.Halt`, no taps, nothing typed, `do_skip` not called.
  - `test_same_name_different_profile_halts`
  - `test_unreadable_label_at_capture_halts` (phone profile)
  - `test_confirm_same_position_unconfirmed`
  - `test_confirm_shifted_third_position_unconfirmed`: compose gone, same profile at position 2 ≠ pre-tap 1 ≠ top → `LikeUnconfirmed`.
  - `test_confirm_top_never_stable_unconfirmed`
  - `test_confirm_genuine_advance_same_name_ok`: confirmed after one stabilising swipe.
  - The existing photo-1 tests keep passing with the identity passed in.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_identity.py tests/test_do_like.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per spec §6, "Identity" and "Send confirmation". Identity failure raises `Halt("identity_mismatch: <reason>")` from inside `do_like`, and `_attempt_like` re-raises `Halt` untouched (place the `except Halt: raise` before the generic `except Exception`). Log `runlog.event("target", target="identity", result=reason)`. `scroll_to_verified_top` swipes up one at a time (bound `FRAMES_PER_PROFILE * 2 + 4`) until two consecutive region hashes are equal; it returns the last PNG, or `None` if the view never settles.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 6: Prompt targeting with pre-tap fallback

**Files:**
- Create: `target.py`
- Modify: `config.py` (`TARGET` dict)
- Modify: `main.py` (`do_like` prompt path; `_like_prompt_xy`)
- Modify: `runlog.py` (`_print_run` prints counts of `target` events from `events.jsonl`)
- Test: `tests/test_target.py`, additions to `tests/test_do_like.py`, `tests/test_runlog.py`

**Interfaces:**
- Consumes: Task 5 `ProfileIdentity`, `identity.skip_label`, `ConfirmBaseline`.
- Produces:
  - `target.normalise(s: str) -> str` (lowercase; curly quotes to straight; strip punctuation, trailing dots and ellipses; collapse spaces)
  - `target.parse_prompt_label(label) -> tuple[str, str] | None` (parses `"Prompt: <q>. Answer: <a>"`)
  - `target.match_score(label, prompt_text, answer_text) -> float`
  - `target.find_prompt_heart(nodes, anchor) -> tuple[ui.UiNode, ui.UiNode, float] | None` (the matched prompt node, its `Like prompt` node, and the score)

- [ ] **Step 1: Write failing tests.**
  `tests/test_target.py`:
  - `"Prompt: This year, I really want to. Answer: Explore......."` matches `prompt_text="This year, I really want to"`, `answer_text="Explore more of the coast"` with score ≥ 0.8;
  - a different prompt with the same answer scores < 0.8;
  - `"I’m"` matches `"I'm"` (Review Focus 3);
  - with two prompts on screen, the heart chosen is the `Like prompt` below the matched one and above the next `Prompt:`;
  - a lone `Like prompt` with no preceding matched prompt → `None`.

  `tests/test_do_like.py` additions:
  - prompt matched on scroll step 2 → the tapped heart is that `Like prompt` center; `pre_tap_viewport == "scrolled"`;
  - heart y > `low_y_frac * 2340` → one nudge scroll and a re-dump before the tap;
  - not found within `max_scroll_steps` → back to top, the photo-1 heart is tapped on the same profile, recovery skip not called, `target` event `fallback_photo1`;
  - Send Like missing after the prompt-heart tap → `LikeAborted("like_aborted_detection")`, exactly one heart tap;
  - the name changes mid-scan → `Halt`;
  - on `DEVICE="emulator"`, the prompt path is skipped (photo 1).

  `tests/test_runlog.py`: `_print_run` output includes `target:` counts when events exist.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_target.py tests/test_do_like.py tests/test_runlog.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per spec §6. Score: `0.6 * difflib ratio(question) + 0.4 * answer_score`, where `answer_score` is 1.0 if the normalised UI answer (truncation stripped) is a prefix of, or contains, the normalised anchor answer, and otherwise the difflib ratio. Taps go only to label-located `Like prompt` / `Like photo` nodes, after confirming the point isn't on a Rose node (reuse `ui._is_rose_node`).
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 7: Eval fixtures, rendering and legacy snapshot

**Files:**
- Create: `evals/openers/README.md`, `evals/openers/profiles.json`, `evals/openers/render.py`, `evals/openers/legacy_prompt.py`, `evals/openers/results/.gitkeep`, `evals/__init__.py`, `evals/openers/__init__.py`
- Modify: `.gitignore` (add `evals/openers/results/*` and `!evals/openers/results/.gitkeep`; rendered frames are already covered by `*.png`)
- Test: `tests/test_eval_render.py`

**Interfaces:**
- Produces:
  - Profile JSON: `{id, name, age, tags: [...], info: str, elements: [{kind: "photo", caption} | {kind: "prompt", prompt, answer}], expected_absent: [str]}`
  - `render.render_profile(p: dict, out_dir: Path, width=540, frame_h=1170) -> list[Path]`
  - `legacy_prompt.SYSTEM_PROMPT` (str) and `legacy_prompt.SCHEMA` (dict): a frozen copy from `git show HEAD:judge_common.py` with the old default voice and the old example_lenient placeholder premade, formatted with the eval preferences.

- [ ] **Step 1: Write failing tests:**
  - every profile in `profiles.json` has a unique id and at least one tag from `{sparse, ambiguous_image, serious, playful, mixed}`;
  - there are ≥ 24 profiles, with ≥ 4 for each tag;
  - `render_profile` writes ≥ 1 PNG of width 540, and frame 0 contains a `Skip <name>`-style header region (test it via pixel non-uniformity in the top 120 px);
  - `legacy_prompt.SYSTEM_PROMPT` contains `"followed by a short question"` (proving it's the old prompt).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_eval_render.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Write 24–28 fictional profiles yourself; don't copy fork or LocalBed data.
  - Sparse: 1–2 photos and 0–1 prompts.
  - Ambiguous image: captions like "blurry group of four at a bar, unclear which is her".
  - Serious: grief, career change, sobriety, faith.
  - Playful: hot takes and jokes.

  Use `expected_absent` for tempting hallucinations ("dog" when only a cat is shown, etc.). The renderer draws photo elements as gray boxes with the wrapped caption text (`ImageDraw`, default font), prompt cards as white rounded boxes, and a name/info header on frame 0, then slices into frames.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 8: Eval runner, CLI backends and report

**Files:**
- Create: `evals/openers/run.py`, `evals/openers/cli_backends.py`, `evals/openers/report.py`
- Test: `tests/test_eval_report.py`, `tests/test_cli_backends.py`

**Interfaces:**
- Consumes: Task 1 `decision_from_dict`; Task 3 `finalize`, `complete_json`; Task 7 render and legacy.
- Produces:
  - `cli_backends.extract_json(text: str) -> dict` (first balanced `{...}`, tolerating code fences)
  - `ClaudeCli(model: str | None)` and `CodexCli(model: str | None)`, each with `vision_json(system, frames: list[Path], schema, name) -> dict` and `complete_json(system, user, schema, name) -> dict`
  - `run.main(argv) -> int` with flags `--backend {anthropic,ollama,claude-cli,codex-cli} --pipeline {legacy,new} [--limit N] [--tags t1,t2] [--out PATH]`
  - JSONL row: `{profile_id, tags, pipeline, backend, decision, message, form, anchor_kind, anchor_label, status, n_facts, n_candidates, usage, seconds, error}`
  - `report.summarise(rows: list[dict], profiles: dict) -> dict`
  - `report.main(argv)` writes `<out>.md` and `<out>.html`

- [ ] **Step 1: Write failing tests:**
  - `extract_json` handles fenced, prose-wrapped and nested JSON, and raises `ValueError` on none.
  - `ClaudeCli` builds `["claude", "-p", <prompt>, "--output-format", "json", "--allowedTools", "Read", "--add-dir", <dir>]` (subprocess faked) and parses `{"result": "...json..."}`. On a parse failure it retries once, then raises.
  - `CodexCli` builds `["codex", "exec", "-i", f1, "-i", f2, ..., <prompt>]` (faked). If `codex exec --help` shows `--output-schema`, the schema file path is passed.
  - `summarise`:
    - coverage is the share of likes with a non-empty message, overall and per tag;
    - quality metrics count non-empty rows only;
    - an all-empty run has coverage 0.0 and `acceptance["passed"] is False`;
    - the question-ending rate is computed;
    - a message containing an `expected_absent` term is flagged;
    - `predicted_target` is `"prompt"` for anchor kind `prompt`, else `"photo1"`;
    - acceptance follows spec §7's thresholds against a legacy summary.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_eval_report.py tests/test_cli_backends.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `run.py` patches `config` in-process: `PREFERENCES = "Decision: like every profile."`, `AGE_MIN/MAX = None`, `MESSAGE_VOICE = None`. For `new`, it uses `PREMADES = []` and `build_system_prompt()` + `DECIDE_INPUT_SCHEMA`, then `decision_from_dict`, `finalize`, and a per-run `OpenerHistory`. For `legacy`, it uses `legacy_prompt` and takes `message` as-is.
  - Anthropic/Ollama vision goes through new small helpers: `judge.vision_json(system, frames_png, schema, name)` and the Ollama equivalent. Refactor `judge.judge` to call it, so no prompt logic is duplicated.
  - The report header states that scores are heuristic model and text checks, not match rates.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: PASS.

### Task 9: Verification, eval run and docs

**Files:**
- Modify: `evals/openers/README.md` (how to run; the claude/codex install-and-login note; the cost note for the API backend)
- Modify: `README.md` (one short "Opener pipeline" paragraph linking the eval README)

- [ ] **Step 1:** Run the full suite: `.venv/bin/python -m pytest tests -q`. Expected: all pass, with a count > the previous count.
- [ ] **Step 2:** Run the smoke test without models: `.venv/bin/python -m evals.openers.render --all` writes frames for every profile. Then run `report.py` on a synthetic fixture JSONL to check the output renders.
- [ ] **Step 3:** Check that the user's CLI is installed: `which claude || which codex`. If neither is present, stop and ask. Otherwise run `--limit 3` per pipeline, inspect the rows, then run the full set with both pipelines.
- [ ] **Step 4:** Run `report.py legacy.jsonl new.jsonl --out evals/openers/results/report`. Read it and pick representative before/after pairs for each tag. Report the acceptance results honestly, including failures.
- [ ] **Step 5:** Run `git status` to confirm `.env`, `debug/`, `*_coords.json`, PNGs and results are not staged or untracked-and-unignored. Don't commit; ask the user.
