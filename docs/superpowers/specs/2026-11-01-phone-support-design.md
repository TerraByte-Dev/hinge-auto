# Physical phone support (Galaxy S23) — design

Date: 2026-11-01 · Branch: `phone-galaxy-s23` · Status: draft for review

## Goal

Run every hinge-auto flow that exists for the emulator on the user's own
Samsung Galaxy S23 instead, reliably enough for regular personal runs, and
make every failure during a run visible and diagnosable after the fact.

Audience: the user only (not an upstream contribution). Generality is added
only where it costs nothing; the emulator keeps working via one config switch.

### Success criteria

1. All flows run on the S23: dry-run loop, live liking with openers,
   `--set-filters`, `--location` / `--rotate`, `scan_self.py`,
   `matches_scan.py`.
2. A like is counted only when Hinge confirms it went out. Nothing ever taps
   a Rose / "Send a Rose" control.
3. A Hinge layout change halts the run with a clear message instead of
   silently skipping profiles.
4. Every run leaves a permanent, self-contained record: per-action events,
   per-profile outcome, flagged cases with incident snapshots, summary.
5. `pytest` passes; each live flow has been rehearsed on the phone.

### Non-goals

- Other phones, iPhone, or upstream PR polish.
- Accessibility-tree-driven navigation for the whole app (used only for
  targeted checks, see §3).
- Changing judge / mode logic.

## Background (established facts)

- Phone: SM-S911B, serial `RZCX920XP8L`, Android 15 / One UI 7,
  1080×2340, density 480. USB debugging authorized.
- Hinge on the phone does **not** set FLAG_SECURE: `adb exec-out screencap`
  returns real frames (verified; window flags lack 0x2000). No macOS window
  capture needed.
- `adb` lives at `~/Library/Android/sdk/platform-tools/adb` and is not on the
  user's shell PATH.
- New Hinge UI on the phone:
  - Photo heart: solid (26,26,26) circle ~144 px with a white glyph.
  - Skip X: floating white circle at (152,1865), fixed while scrolling.
  - Compose card is inline under the tapped photo, opens with the keyboard
    up and the comment field focused. Comment box spans ~200 px above the
    Send pill center. Lavender (188,167,192) Rose pill at x 108–408; flat
    (26,26,26) "Send Priority Like" pill at x 444–972 (528×123). The
    keyboard toolbar strip is the same grey but full-width.
  - One Back hides the keyboard and leaves the card; Back with no keyboard
    navigates out of Hinge.
  - **After Send, Hinge may show a "Send a Rose instead?" bottom sheet**
    with buttons "Send a Rose" (paid) and "Send Like anyway"; a full-screen
    "Close sheet" scrim sits behind it. Observed on the first live send,
    where the old code reported the like as sent.

## Already implemented on the branch

Committed (`c3209d4`): device profile in `config.py` (`DEVICE`,
`ADB_SERIAL`, `DEVICES`, `_apply_device`), adb `-s <serial>` targeting and
`check_device` serial check, dark-heart detection with 13×13 opening,
S23 coords for skip/heart/nav/filter chips, `DRY_RUN = True`.

Uncommitted (Section 2 work, to be committed with this spec):
- `vision.find_send_like` detects the black pill (width 400–700, height
  90–150, x center > 500); peach branch kept.
- `config.COMPOSE` (`comment_dy`, `text_window`) per device; used by
  `find_comment_input` and `comment_field_text_pixels` (full card width).
- `do_like` raises instead of sending when typed text didn't land.
- `adb.keyboard_shown`, `adb.hide_keyboard` (Back only if keyboard up);
  `main.recover_from_failed_like` (hide keyboard → skip) used by the loop.
- Tests: 19 passing (`tests/test_adb.py`, `test_vision.py`,
  `test_compose.py`, `test_do_like.py`).

## Design

### 1. Configuration

- `DEVICE = "galaxy_s23"` is the default; `"emulator"` restores the old
  values. The S23 profile holds `serial`, `screen`, `coords`, `compose`.
- New `COORDS` keys (emulator values in the base dict, phone values in the
  profile): `self_avatar`, `view_tab`, `back_arrow` (phone values measured),
  `matches_scroll_from`, `matches_scroll_to`, `matches_expand_their_turn`
  (moved out of hardcoded `matches_scan.py` values `(540,1900)`,
  `(540,1500)`, `(540,1264)`).
- Per-flow JSON files (`filter_coords.json`, `location_coords.json`,
  `matches_coords.json`) stay gitignored and are generated on the phone.
  Each loader compares the file's `screen_size` to
  `(config.SCREEN_WIDTH, config.SCREEN_HEIGHT)` and raises
  `RuntimeError("<file> was calibrated for WxH, current device is WxH —
  recalibrate")` on mismatch.
- `adb.py` resolves the binary once: `shutil.which("adb")`, else
  `~/Library/Android/sdk/platform-tools/adb`, else raise with that path in
  the message.

### 2. Live liking (implemented; listed for completeness)

See "Already implemented". Remaining: §3a post-send confirmation.

### 3. Layout guard and safety checks

New module `ui.py` (screen-state checks; keeps `vision.py` pixel-only):

- `ui_texts() -> list[UiNode]` — runs `uiautomator dump` to
  `/sdcard/hinge_ui.xml`, pulls it via `exec-out cat`, parses `text`,
  `content-desc`, `bounds` into `UiNode(label, x0, y0, x1, y1)`. ~1–2 s;
  called only at the checkpoints below, never per frame.
- `find_label(nodes, text) -> UiNode | None` — exact, case-sensitive match
  on `text` or `content-desc`.
- `safe_tap_label(nodes, text)` — taps the center of `text`'s node after
  asserting no node whose label contains "Rose" contains that point; raises
  `UnsafeTap` otherwise. This is the only way the code taps a sheet button.

#### 3a. Post-send confirmation (`do_like`)

After the Send tap and `after_like_sent` delay:

1. `nodes = ui.ui_texts()`.
2. If `find_label(nodes, "Send Like anyway")`: log `rose_upsell_sheet`,
   `safe_tap_label(nodes, "Send Like anyway")`, wait, re-read nodes. If the
   label is still present → raise `LikeUnconfirmed("rose sheet persisted")`.
3. If `find_label(nodes, "Close sheet")` (any other sheet) → raise
   `LikeUnconfirmed("unknown sheet")`; do not tap anything.
4. Screenshot: confirmed when `vision.find_send_like(png) is None` **and**
   `_profile_region_hash(png)` differs from the hash captured before the
   heart tap. Otherwise raise `LikeUnconfirmed("profile did not advance")`.
5. `do_like` returns an outcome string: `liked_confirmed` or
   `like_sheet_handled`.

`LikeUnconfirmed` is **not** recovered by skip (state is unknown and a skip
tap could land on a sheet): the loop records the outcome, snapshots, and
halts.

#### 3b. Startup check (`main.preflight()`)

Before the loop: screenshot + `ui_texts()`. Pass when the skip-X patch
matches its fingerprint, `find_first_heart` returns a point, and no
"Close sheet" node exists. Fingerprint = dark/white pixel counts in the
120×120 patch at `skip_button`, stored in the device profile as
`skip_fingerprint` (S23: dark 1657, white 12500; match within ±10%).
Failure → print "Hinge layout looks different (…reason…). Recalibrate or
dismiss popups." and exit 2.

#### 3c. Halt counters (main loop)

Halt (not skip) when any of:
- 2 consecutive `like_aborted_detection` outcomes,
- 3 consecutive `duplicate` outcomes,
- any `like_unconfirmed` or `unsafe_tap`.

Halting writes the incident (§4c) and summary, prints the reason, exits 1.

#### 3d. Scripted flows

Each flow checks one label after its first navigation tap and raises
`LayoutChanged(expected, seen_labels[:20])` if missing:

| Flow | After | Expect label |
|---|---|---|
| `locations.set_location` | `sliders_icon` | "My neighborhood" |
| `filters.set_age_range` | `age_chip` | "Age" sheet title (exact text captured during calibration) |
| `scan_self.navigate_to_self_view` | `nav_self_pfp` | "View" |
| `matches_scan.open_matches_tab` | `matches_tab` | "Your turn" or "Their turn" |

Exact label strings are captured from `ui_texts()` on the phone during
implementation and stored as constants next to each flow.

#### 3e. Out-of-candidates detection

`locations.is_out_of_candidates` samples a flat dark patch at emulator
coordinates; on the phone the (26,26,26) heart can sit in that patch →
false positive → spurious city rotation. New rule: out-of-candidates is
true only when the pixel pre-check passes (kept as a cheap filter) **and**
`ui_texts()` contains the empty-state label stored in
`locations.EMPTY_STATE_LABEL`. That constant is captured from the phone the
first time the empty state is seen; while it is `None`, `--rotate` refuses
to start on a non-emulator device with a message saying so.

### 4. Observability

New module `runlog.py`. All paths under gitignored `debug/runs/`.
**Kept permanently — no pruning.** Disk note: frames are ~0.8 MB each,
~5–6 MB per profile with `SAVE_DEBUG_FRAMES`, ~0.5 GB per 100 profiles.

#### 4a. Per-run folder `debug/runs/<YYYYmmdd-HHMMSS>/`

- `events.jsonl` — one line per action:
  `{ts, profile, step, kind, target, xy, result, detail}`.
  `kind` ∈ `tap | swipe | key | screenshot | detect | ui_dump | sheet |
  outcome | error`. `target` is a COORDS/semantic name (`skip_button`,
  `send_like`, `heart`, `send_like_anyway`…). Detection lines include the
  result and, on `None`, the rejection summary (§4d).
- `run.log` — Python `logging` at DEBUG to file, INFO to console. The
  loop's `print`s become `log.info`.
- `summary.json` — written in a `finally` (normal end, halt, Ctrl-C,
  crash): counts per outcome, flagged list, cost, timings, device, serial,
  config snapshot (`MODE_NAME`, `DRY_RUN`, caps, `DEVICE`, `MODEL`), git
  commit, exit reason.

Instrumentation lives in `adb._run` (every tap/swipe/key/screencap logged
with caller-provided `target`), detector wrappers, and the loop.
`adb.tap(x, y, target=None)` gains an optional `target` kwarg.

#### 4b. Per-profile outcome

`liked_confirmed`, `like_sheet_handled`, `like_unconfirmed`,
`like_aborted_text`, `like_aborted_detection`, `skipped`,
`skip_unconfirmed` (frame hash unchanged after skip), `duplicate`,
`judge_failed`, `unsafe_tap`.

- Only `liked_confirmed` and `like_sheet_handled` increment `likes_sent`.
- Flagged = anything except `liked_confirmed`, `skipped`.
  (`like_sheet_handled` is flagged as informational.)
- The existing `debug/session_log.jsonl` record gains `outcome` and
  `run_id` fields (append-only, backward compatible).

#### 4c. Incident snapshots

On every flagged outcome or exception:
`debug/runs/<id>/incidents/<NN>-<outcome>/` containing `before.png`,
`after.png` (screens around the failing step), `ui.json` (`ui_texts()`
result), `detect.json` (§4d explain output for heart and send-like on
`after.png`), `events_tail.jsonl` (last 20 events), `error.txt`
(exception + traceback if any).

#### 4d. Detector explain mode

`find_first_heart(png, explain=False)` / `find_send_like(png,
explain=False)`: with `explain=True` return `(result, report)` where
`report` lists every blob considered — mask (`white|dark_grey|peach|
black_pill`), bbox, area, and the first failed rule (`size`, `aspect`,
`area`, `x<=threshold`) or `accepted`. Default return type unchanged.

#### 4e. End-of-run report

Console block after the totals:
```
FLAGGED (2):
  profile 4  like_unconfirmed   debug/runs/<id>/incidents/01-like_unconfirmed
  profile 9  skip_unconfirmed   debug/runs/<id>/incidents/02-skip_unconfirmed
```
`python -m runlog last` (and `runlog <id>`) prints summary + flagged list.

#### 4f. Privacy

Run folders contain other people's profiles; they stay in gitignored
`debug/`. The config snapshot excludes any name containing `KEY`, `TOKEN`
or `SECRET`; log formatting redacts `sk-ant-[A-Za-z0-9_-]+`.

### 5. Calibration on the phone

- `calibrate_filters.py` and `calibrate_matches.py` run against the phone
  as-is (they use `adb` and `config.SCREEN_*`); fix any emulator constants
  they hardcode (`_DEFAULT_SLIDER_Y`, `_DEFAULT_DISMISS_TAP`) by measuring
  on the phone.
- `location_coords.json` filled from phone screenshots + `ui_texts()`
  bounds (labels give exact tap centers).
- `calibrate.py` prints the S23 profile keys and points at `ui_texts()` as
  the fastest way to read coordinates.

## Error handling summary

| Situation | Behavior |
|---|---|
| Heart / Send pill not found | `like_aborted_detection`, hide keyboard → skip; halt on 2nd in a row |
| Typed text didn't land | `like_aborted_text`, hide keyboard → skip |
| Rose upsell sheet | tap "Send Like anyway" via `safe_tap_label` → `like_sheet_handled` |
| Unknown sheet / no advance after send | `like_unconfirmed`, incident, halt |
| Tap point inside a Rose element | `unsafe_tap`, incident, halt |
| Skip didn't advance | `skip_unconfirmed`; existing duplicate path; halt on 3rd duplicate |
| Scripted flow on wrong screen | `LayoutChanged`, incident, exit |
| JSON coords for other screen size | `RuntimeError` before any tap |
| Preflight fails | exit 2 before any tap |

## Testing

- pytest with fakes (no device): `ui_texts` XML parsing; `safe_tap_label`
  refusing points inside "Send a Rose"; post-send branches (sheet handled,
  sheet persisted, unknown sheet, no advance, confirmed); outcome →
  counters/flags; halt counters; preflight pass/fail; `screen_size`
  mismatch; adb path resolution; `explain` reports; `summary.json` written
  on exception; config snapshot redaction.
- Phone rehearsals (Send/Apply/commit taps intercepted): like flow incl.
  post-send path with a forced fake sheet; preflight; each scripted flow's
  label check; forced detector failure produces an incident folder.
- Live checks, run by the user (account-changing): one real like (expect
  `liked_confirmed` or `like_sheet_handled`), one filter change, one
  location change; `scan_self` and one matches scan (read-only) can be run
  by Claude.

## Open items resolved during implementation

- Exact label strings for §3d and the empty-state screen (§3e) are read
  from the phone with `ui_texts()`; they are data, not design decisions.
