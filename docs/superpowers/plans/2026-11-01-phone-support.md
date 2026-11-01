# Physical Phone Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every hinge-auto flow runs on the user's Galaxy S23 with confirmed likes, Rose-safe taps, a layout guard that halts instead of silently skipping, and a permanent per-run record of every action and failure.

**Architecture:** Builds on the device profile already in `config.py`. Two new modules: `ui.py` (accessibility-tree reads and label-safe taps, used only at checkpoints) and `runlog.py` (per-run folder: events, outcomes, incidents, summary). `main.py`'s loop switches from like/skip booleans to explicit outcomes that drive counters, halts and incidents.

**Tech Stack:** Python 3.12 (`.venv`), adb, numpy/scipy/Pillow, pytest (installed in `.venv`), stdlib `logging`, `xml.etree.ElementTree`.

**Spec:** `docs/superpowers/specs/2026-11-01-phone-support-design.md`

## Global Constraints

- Branch `phone-galaxy-s23`; run tests with `.venv/bin/python -m pytest tests -q` from `hinge-auto/`.
- Device: serial `RZCX920XP8L`, 1080×2340. Phone commands need `export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"` until Task 1 lands.
- Never tap a Rose / "Send a Rose" control. Sheet buttons are tapped only through `ui.safe_tap_label`.
- `DRY_RUN = True` stays the committed default. Account-changing live checks (real like, filter Apply, location commit) are run by the user, not the agent.
- Never print `.env` or key values; logs redact `sk-ant-[A-Za-z0-9_-]+`; config snapshot drops names containing `KEY`, `TOKEN`, `SECRET`.
- Run logs live under gitignored `debug/runs/` and are kept permanently (no pruning).
- Screenshots of other people's profiles never go into the repo or tests; tests use synthetic images/XML.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.env`, `debug/`, or `*_coords.json`.

## Review Focus

1. **Sheet appears late** (after the post-send delay, or after "Send Like anyway") → must still be caught, not counted as liked. Test in Task 6: sheet present on second read → `LikeUnconfirmed`.
2. **Ctrl-C mid-profile** → `summary.json` still written with exit reason `interrupted`. Test in Task 7.
3. **`uiautomator dump` fails or returns empty** (it errors while animations run) → treat as "unknown", never as "no sheet". Test in Task 3 (`ui_texts` raises `UiDumpFailed` on empty/invalid XML) and Task 6 (dump failure after send → `like_unconfirmed`).
4. **Label tap point overlapping a Rose node** (sheet layouts shift) → refuse. Test in Task 3 (`safe_tap_label` raises `UnsafeTap` when the label center lies inside a "Send a Rose" node's bounds).
5. **Stale `*_coords.json` from another device** → refuse before any tap. Test in Task 2.

---

### Task 1: adb binary resolution

**Files:**
- Modify: `adb.py` (`_run`)
- Test: `tests/test_adb.py`

**Interfaces:**
- Produces: `adb.ADB_FALLBACK: Path = Path.home() / "Library/Android/sdk/platform-tools/adb"`; `adb._adb_binary() -> str` (cached).

- [ ] **Step 1: Write failing tests** `test_adb_binary_prefers_path` (monkeypatch `shutil.which` → `"/usr/bin/adb"`; assert `_adb_binary() == "/usr/bin/adb"`), `test_adb_binary_falls_back_to_sdk` (which → None, `ADB_FALLBACK` set to a tmp file that exists; assert returns that path), `test_adb_binary_missing_raises` (which → None, fallback missing; `pytest.raises(RuntimeError, match="platform-tools/adb")`). Clear the cache in each test (`adb._adb_binary.cache_clear()`).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_adb.py -q` → 3 new FAIL (`AttributeError: _adb_binary`).
- [ ] **Step 3: Implement** `_adb_binary` with `functools.cache`; `_run` builds `cmd = [_adb_binary()]`. Update existing tests' expected `cmd[0]` by monkeypatching `adb._adb_binary` to return `"adb"` in `FakeRun`-based tests.
- [ ] **Step 4: Run** full suite → all PASS.
- [ ] **Step 5: Commit** `fix: find adb in the Android SDK when it isn't on PATH`.

### Task 2: Device-checked coordinate JSON + moved hardcoded coords

**Files:**
- Modify: `config.py` (new helper + COORDS keys), `filters.py:39-44`, `locations.py:42-47`, `matches_scan.py:55-62,84,102`
- Test: `tests/test_device_json.py`

**Interfaces:**
- Produces: `config.load_device_json(path: Path, calibrate_hint: str) -> dict` — raises `RuntimeError` if missing (message includes `calibrate_hint`) or if `data["screen_size"] != [SCREEN_WIDTH, SCREEN_HEIGHT]` (message: `"{name} was calibrated for {w}x{h}, current device is {W}x{H} — recalibrate"`).
- Produces COORDS keys: `matches_scroll_from` (540,1900), `matches_scroll_to` (540,1500), `matches_expand_their_turn` (540,1264) in the base dict (emulator values).

- [ ] **Step 1: Write failing tests** `test_load_device_json_ok` (tmp file with `screen_size: [1080, 2340]`, `config.SCREEN_*` monkeypatched to 1080/2340 → returns dict), `test_load_device_json_wrong_screen` (`[1080, 2424]` → raises, match `"1080x2424"` and `"1080x2340"`), `test_load_device_json_missing` (match the hint).
- [ ] **Step 2: Run** → FAIL (`AttributeError`).
- [ ] **Step 3: Implement** helper; switch `filters.load_coords`, `locations.load_coords`, `matches_scan._load_coords` to it (matches keeps raising `SystemExit` by wrapping: `except RuntimeError as e: raise SystemExit(str(e))`). Replace hardcoded matches swipe/tap with the new COORDS keys.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: reject coord files calibrated for another screen; move matches coords to config`.

### Task 3: `ui.py` — accessibility-tree reads and Rose-safe taps

**Files:**
- Create: `ui.py`
- Test: `tests/test_ui.py` (synthetic XML strings modelled on the real sheet: nodes "Send a Rose instead?", "Send a Rose" bounds `[405,1832][674,1891]` inside button `[333,1790][746,1934]`, "Send Like anyway" `[345,2011][734,2070]`, content-desc "Close sheet" `[0,0][1080,2340]`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class UiNode: label: str; x0: int; y0: int; x1: int; y1: int` with `center -> tuple[int, int]` and `contains(x, y) -> bool`.
  - `class UiDumpFailed(RuntimeError)`, `class UnsafeTap(RuntimeError)`.
  - `parse_ui_xml(xml: str) -> list[UiNode]` — one node per non-empty `text` or `content-desc` (both if both present); raises `UiDumpFailed` on empty or unparsable input.
  - `ui_texts() -> list[UiNode]` — `adb shell uiautomator dump /sdcard/hinge_ui.xml` then `adb exec-out cat /sdcard/hinge_ui.xml` via `adb._run`; one retry after 1 s on failure; then `UiDumpFailed`.
  - `find_label(nodes, text) -> UiNode | None` — exact match.
  - `safe_tap_label(nodes, text, target: str) -> tuple[int, int]` — `UnsafeTap` if the label is missing or its center lies inside any *Rose node* (label contains `"Rose"` and does not end with `"?"`, so the sheet title is not one); else `adb.tap(*center, target=target)` and return center.

- [ ] **Step 1: Write failing tests** `test_parse_extracts_text_and_desc`, `test_parse_empty_raises`, `test_parse_garbage_raises`, `test_find_label_exact_only` ("Send Like" must not match "Send Like anyway"), `test_safe_tap_taps_send_like_anyway_center` (fake `adb.tap` records `(539, 2040)`), `test_safe_tap_refuses_point_inside_rose` (move "Send Like anyway" bounds inside the Rose button → `UnsafeTap`, no tap), `test_safe_tap_missing_label_raises`, `test_ui_texts_retries_then_raises` (fake `adb._run` returns `b""` twice → `UiDumpFailed`, two dump attempts).
- [ ] **Step 2: Run** → FAIL (`ModuleNotFoundError: ui`).
- [ ] **Step 3: Implement** `ui.py`. `adb.tap` gains `target: str | None = None` (ignored until Task 5).
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Phone check (read-only):** with Hinge on Discover, `.venv/bin/python -c "import ui; print([n.label for n in ui.ui_texts()][:30])"` prints visible labels (chips "Signals", "Age"…). No taps.
- [ ] **Step 6: Commit** `feat: ui.py — uiautomator label reads and Rose-safe label taps`.

### Task 4: Detector explain mode

**Files:**
- Modify: `vision.py` (`find_first_heart`, `find_send_like`, `_round_blobs`, `_pill_blobs`)
- Test: `tests/test_vision.py`

**Interfaces:**
- Produces: `find_first_heart(png, explain=False)` and `find_send_like(png, explain=False)`; with `explain=True` return `(result, report: list[dict])`, each dict `{"mask": str, "bbox": [x0,y0,x1,y1], "area": int, "verdict": "accepted"|"size"|"aspect"|"area"|"x<=800"|"x<=500"}`. Masks: `white`, `dark_grey`, `peach`, `black_pill`. Blobs smaller than 30 px in both dimensions are omitted from the report.

- [ ] **Step 1: Write failing tests** `test_explain_heart_reports_accepted_blob` (on `_new_ui_frame`, one entry with `verdict == "accepted"` and `mask == "dark_grey"`), `test_explain_send_like_reports_keyboard_strip_rejected_by_size` (on `_compose_frame(send=False)`, an entry with `mask == "black_pill"`, bbox width 1080, `verdict == "size"`), `test_explain_default_return_unchanged` (no `explain` → tuple of two ints).
- [ ] **Step 2: Run** → FAIL (`TypeError: unexpected keyword 'explain'`).
- [ ] **Step 3: Implement**: blob helpers take an optional `report: list | None` and append one dict per considered blob.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: vision explain mode for incident diagnostics`.

### Task 5: `runlog.py` — run folder, events, incidents, summary

**Files:**
- Create: `runlog.py`
- Modify: `adb.py` (`_run`, `tap`, `swipe`, `screenshot`, `hide_keyboard` emit events), `config.py` (`RUNS_DIR = DEBUG_DIR / "runs"`)
- Test: `tests/test_runlog.py`

**Interfaces:**
- Produces (module-level "current run", `None` when no run is active so scripts/tests work unchanged):
  - `start_run(runs_dir: Path | None = None) -> Path` — creates `<runs_dir>/<YYYYmmdd-HHMMSS>/`, configures `logging` (file `run.log` DEBUG, console INFO, redaction filter), returns the folder. `run_id()` returns the folder name.
  - `event(kind: str, target: str | None = None, xy=None, result=None, detail=None, step: str | None = None) -> None` — appends to `events.jsonl` with `ts` and current `profile` (set via `set_profile(n: int)`); also kept in an in-memory deque of 20.
  - `incident(outcome: str, before: bytes | None, after: bytes | None, error: BaseException | None = None) -> Path` — writes `incidents/<NN>-<outcome>/` with `before.png`, `after.png`, `ui.json` (best-effort `ui.ui_texts()`, error string on failure), `detect.json` (explain reports on `after`), `events_tail.jsonl`, `error.txt`.
  - `record_outcome(profile: int, outcome: str, incident_dir: Path | None) -> None`; `FLAGGED_OK = {"liked_confirmed", "skipped"}`; `LIKE_OUTCOMES = {"liked_confirmed", "like_sheet_handled"}`.
  - `write_summary(exit_reason: str, totals: dict) -> Path` — `summary.json` with outcome counts, flagged list `[{profile, outcome, incident}]`, totals, `config_snapshot()`, device serial, `git rev-parse --short HEAD`, exit reason.
  - `config_snapshot() -> dict` — public uppercase `config` attributes of JSON-serializable types, minus names containing `KEY`/`TOKEN`/`SECRET`, minus `PREFERENCES`.
  - `flagged_report() -> str` — the console block from spec §4e (empty string when none).
  - `redact(s: str) -> str`.

- [ ] **Step 1: Write failing tests** (all with `tmp_path`): `test_start_run_creates_folder_and_log`, `test_event_appends_jsonl_with_profile`, `test_adb_tap_emits_event_with_target` (fake subprocess; `adb.tap(1, 2, target="skip_button")` → event kind `tap`, target `skip_button`, xy `[1, 2]`), `test_no_active_run_events_are_noop`, `test_incident_writes_bundle` (fake `ui.ui_texts` raising `UiDumpFailed` → `ui.json` contains the error, other files present), `test_summary_counts_and_flags` (outcomes liked_confirmed, skipped, like_unconfirmed → flagged list has 1), `test_config_snapshot_drops_secrets` (monkeypatch `config.ANTHROPIC_API_KEY="sk-ant-x"`, `config.SOME_TOKEN="t"` → absent), `test_redact_masks_anthropic_keys`, `test_flagged_report_format` (contains `"FLAGGED (1):"` and the incident path).
- [ ] **Step 2: Run** → FAIL (`ModuleNotFoundError: runlog`).
- [ ] **Step 3: Implement** `runlog.py`; wire `adb` functions to call `runlog.event` (screenshot events log byte size only, not data).
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: runlog — per-run events, incidents and summary`.

### Task 6: Post-send confirmation in `do_like`

**Files:**
- Modify: `main.py` (`do_like`, new exceptions)
- Test: `tests/test_do_like.py`

**Interfaces:**
- Consumes: `ui.ui_texts`, `ui.find_label`, `ui.safe_tap_label`, `ui.UiDumpFailed`, `ui.UnsafeTap` (Task 3); `runlog.event` (Task 5).
- Produces: `do_like(message: str = "") -> str` returning `"liked_confirmed"` or `"like_sheet_handled"` (dry run returns `"skipped"`). Exceptions: `class LikeAborted(RuntimeError)` with attribute `outcome` ∈ {`like_aborted_detection`, `like_aborted_text`}; `class LikeUnconfirmed(RuntimeError)` (outcome `like_unconfirmed`). Existing heart/send-pill/text raises become `LikeAborted` with the matching outcome. `ui.UnsafeTap` propagates unchanged.

- [ ] **Step 1: Write failing tests** reusing the existing fake setup (extract it into a fixture `live_like` returning the taps list and a settable `ui_sequence`, `hash_sequence`, `send_after`):
  - `test_confirmed_when_no_sheet_and_profile_advanced` → returns `"liked_confirmed"`.
  - `test_rose_sheet_taps_send_like_anyway` (first `ui_texts` has the sheet, second doesn't) → returns `"like_sheet_handled"`; taps include `(539, 2040)`; no tap inside Rose bounds.
  - `test_rose_sheet_persisting_raises_unconfirmed`.
  - `test_unknown_sheet_raises_unconfirmed_without_tapping` ("Close sheet" only; taps after send == []).
  - `test_ui_dump_failure_after_send_is_unconfirmed`.
  - `test_profile_not_advanced_raises_unconfirmed` (same hash before/after).
  - `test_send_pill_still_visible_raises_unconfirmed`.
  - Update `test_do_like_aborts_instead_of_sending_when_text_missing` to expect `LikeAborted` with `outcome == "like_aborted_text"`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §3a: capture `main._profile_region_hash` of a screenshot before the heart tap; after Send + `after_like_sent`, run steps 1–5. Emit `runlog.event("sheet", …)` when the Rose sheet is handled.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Phone rehearsal (no send):** adapt the earlier intercept script — intercept the Send tap, then call the post-send checker with a canned `ui_texts` sequence containing the Rose sheet *and* `adb.tap` intercepted; assert it would tap "Send Like anyway" at its center. Record the result in the task report.
- [ ] **Step 6: Commit** `feat: confirm likes after send and handle the Rose upsell sheet`.

### Task 7: Loop outcomes, halts, summary, flagged report

**Files:**
- Modify: `main.py` (`main`, `do_skip`, `recover_from_failed_like`), `metrics.py` (`log_profile`)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: Task 5 `runlog.*`, Task 6 exceptions/returns.
- Produces: `main.Halt(RuntimeError)` with `reason: str`; `do_skip() -> str` returning `"skipped"` or `"skip_unconfirmed"` (frame hash unchanged after skip); `metrics.log_profile(profile_idx, decision, timing, log_path=None, outcome: str | None = None, run_id: str | None = None)` adding `outcome` and `run_id` keys.
- Halt rules (spec §3c): 2 consecutive `like_aborted_detection`; 3 consecutive `duplicate`; any `like_unconfirmed` or `unsafe_tap`. Halt writes an incident, then summary, exit code 1. `likes_sent` increments only for `runlog.LIKE_OUTCOMES`.

- [ ] **Step 1: Write failing tests** driving `main.main()` with fakes for `capture_profile`, `judge`, `do_like`, `do_skip`, `preflight` (no-op), `time.sleep`, and `runlog.start_run(tmp_path)`:
  - `test_like_counted_only_when_confirmed` (do_like returns `like_sheet_handled` then raises `LikeUnconfirmed`) → likes 1, exit 1, summary exit reason starts `"halt"`.
  - `test_two_detection_aborts_in_a_row_halt`.
  - `test_three_duplicates_halt` (capture returns identical frames).
  - `test_unsafe_tap_halts`.
  - `test_summary_written_on_keyboard_interrupt` (judge raises `KeyboardInterrupt`) → `summary.json` exists, exit reason `"interrupted"`.
  - `test_session_log_has_outcome_and_run_id`.
  - `test_flagged_report_printed` (capsys contains `"FLAGGED"`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: `main()` calls `runlog.start_run()` first, wraps the loop in `try/except Halt/KeyboardInterrupt/Exception … finally: write_summary + print flagged_report`; replaces `print` with `logging` (`log = logging.getLogger("hinge")`); records an outcome per profile; incidents on every flagged outcome.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: per-profile outcomes, halt rules and run summary in the loop`.

### Task 8: Preflight

**Files:**
- Modify: `config.py` (S23 profile `skip_fingerprint: {"dark": 1657, "white": 12500}`; emulator: none → preflight skips that check), `main.py` (`preflight`)
- Test: `tests/test_preflight.py`

**Interfaces:**
- Produces: `main.preflight(png: bytes, nodes: list[UiNode]) -> list[str]` (list of failure reasons; empty = pass) and `main.skip_patch_counts(png) -> dict` (`dark` = max channel < 60, `white` = min channel > 235, in the 120×120 patch centered on `COORDS["skip_button"]`). Match = each count within ±10% of the fingerprint. `main()` calls it before the loop; non-empty → log "Hinge layout looks different (…). Recalibrate or dismiss popups." and exit 2.

- [ ] **Step 1: Write failing tests** `test_preflight_passes_on_good_frame` (synthetic frame: `_new_ui_frame` plus a white circle with a dark X drawn so counts hit the fingerprint — compute expected counts from the drawing, then set the fingerprint to them via monkeypatch), `test_preflight_flags_moved_skip_button`, `test_preflight_flags_missing_heart`, `test_preflight_flags_open_sheet` (nodes contain "Close sheet").
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** full suite → PASS; **phone check (read-only):** `.venv/bin/python -c "import adb, ui, main; print(main.preflight(adb.screenshot(), ui.ui_texts()))"` on Discover prints `[]`.
- [ ] **Step 5: Commit** `feat: preflight layout check before the loop`.

### Task 9: Scripted flows on the phone (scan_self, matches, filters, location)

**Files:**
- Modify: `config.py` (S23 `self_avatar`, `view_tab`, `back_arrow`, `matches_*`), `scan_self.py`, `matches_scan.py`, `filters.py`, `locations.py`, `calibrate_filters.py:33-36`
- Create: `ui.py` addition `require_label(expected: str | tuple[str, ...], step: str) -> list[UiNode]` raising `class LayoutChanged(RuntimeError)` with `expected` and the first 20 seen labels.
- Test: `tests/test_ui.py` (`require_label` pass/fail), `tests/test_flows.py`

**Interfaces:**
- Consumes: `ui.ui_texts`, `runlog.event`.
- Produces: label constants `scan_self.VIEW_LABEL`, `matches_scan.MATCHES_LABELS`, `filters.AGE_SHEET_LABEL`, `locations.NEIGHBORHOOD_LABEL`, each read from the phone with `ui_texts()` (data, per spec "Open items").

- [ ] **Step 1: Measure on the phone (read-only navigation + Back):** for each flow, navigate by hand-scripted taps to the screen, run `ui_texts()`, record label strings and the bounds' centers for every coordinate the flow uses. Record the S23 values in the `galaxy_s23` profile (COORDS) and the label constants. Replace `calibrate_filters` `_DEFAULT_*` constants with values read from `config.COORDS` (`age_chip`) or measured phone values (`slider_y`, `apply_button`, `dismiss_tap`) stored in the device profile.
- [ ] **Step 2: Write failing tests** `test_require_label_passes`, `test_require_label_raises_with_seen_labels`, and per flow one test with fakes asserting the flow calls `require_label(<constant>)` right after its first navigation tap and raises `LayoutChanged` before any further tap when absent.
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** the `require_label` calls. Update `calibrate.py` to print the active `DEVICE` profile's COORDS keys and to dump `ui_texts()` labels with their centers next to the saved screenshot (spec §5).
- [ ] **Step 5: Run** full suite → PASS.
- [ ] **Step 6: Phone runs:** `python scan_self.py` end to end (read-only; report saved); `python calibrate_matches.py` then `python matches_scan.py --dry-run`; `python calibrate_filters.py` (dismisses without applying); location flow rehearsed with the final back-arrow commit tap intercepted. Write `location_coords.json` from measured values.
- [ ] **Step 7: Commit** `feat: S23 coords and screen checks for scan_self, matches, filters and location` (do not add `*_coords.json`).

### Task 10: Out-of-candidates rule

**Files:**
- Modify: `locations.py` (`is_out_of_candidates`, `EMPTY_STATE_LABEL: str | None = None`), `main.py` (`--rotate` guard)
- Test: `tests/test_locations.py`

**Interfaces:**
- Produces: `is_out_of_candidates(png: bytes, nodes: list[UiNode] | None = None) -> bool` — on `config.DEVICE != "emulator"`, true only if the pixel check passes **and** `EMPTY_STATE_LABEL` is in `nodes`; emulator behavior unchanged. `main()` with `--rotate`, `DEVICE != "emulator"` and `EMPTY_STATE_LABEL is None` → exit 2 with message `"--rotate needs locations.EMPTY_STATE_LABEL captured from the phone's 'seen everyone' screen"`.

- [ ] **Step 1: Write failing tests** `test_heart_in_patch_is_not_out_of_candidates_on_phone` (`_new_ui_frame` with the heart centered at (850,1475), nodes without the label → False), `test_out_of_candidates_with_label_on_phone`, `test_emulator_behavior_unchanged`, `test_rotate_refuses_without_label`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Loop passes `ui.ui_texts()` only when the pixel check is true (keeps per-profile cost at zero normally).
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `fix: don't mistake the phone's dark heart for the empty-state screen`.

### Task 11: `python -m runlog` CLI

**Files:**
- Modify: `runlog.py` (`main(argv: list[str] | None = None) -> int`, `if __name__ == "__main__"`)
- Test: `tests/test_runlog.py`

**Interfaces:**
- Produces: `python -m runlog last` and `python -m runlog <run_id>` print summary counts, exit reason, and `flagged_report()`; unknown id → exit 1 with `"no run <id> under debug/runs"`.

- [ ] **Step 1: Write failing tests** `test_cli_last_prints_latest_summary`, `test_cli_unknown_run_exits_1` (two fake run folders in `tmp_path`; monkeypatch `config.RUNS_DIR`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: runlog CLI to inspect past runs`.

### Task 12: End-to-end verification on the phone

**Files:** none (verification only; update `CLAUDE.md` "Run it" section for phone usage if commands changed)

- [ ] **Step 1:** Full suite: `.venv/bin/python -m pytest tests -q` → all PASS.
- [ ] **Step 2:** Dry run, 3 profiles, without PATH export: `.venv/bin/python -c "import sys; sys.argv=['main.py']; import config; config.MAX_PROFILES_PER_SESSION=3; import main; main.main()"` → preflight passes, 3 outcomes `skipped`, `debug/runs/<id>/summary.json` exists, `python -m runlog last` shows it.
- [ ] **Step 3:** Forced failure: same run with `vision.find_send_like` patched to return None and `DRY_RUN=False`, `MAX_LIKES_PER_SESSION=1`, judge forced to "like" → outcome `like_aborted_detection`, incident folder with `before.png`, `after.png`, `detect.json`; profile skipped; nothing sent.
- [ ] **Step 4 (user runs):** one real like (`DRY_RUN=False`, `MAX_LIKES_PER_SESSION=1`) → `liked_confirmed` or `like_sheet_handled`; one `--set-filters` run; one `--location <city>` run. Check each with `python -m runlog last`.
- [ ] **Step 5:** Update `~/dating/CLAUDE.md` (local notes, outside the repo — no commit) "Run it" / "Current state" to describe the phone setup, the `phone-galaxy-s23` branch, and `python -m runlog last`.
