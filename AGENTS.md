# Agent instructions

You are assisting a user who just cloned this repo and wants to run it.
Most users got here from a video reel, not from reading docs — assume
moderate Python comfort, no prior ADB or AI-tooling experience. Walk
them through setup interactively; don't just dump command blocks.

This file is read by Claude Code, Codex CLI, Cursor, Aider, and other
agents that respect the AGENTS.md convention.

## What this project is

A Hinge automation loop. An Android emulator runs Hinge; this repo
drives it via ADB and Claude (or Ollama). For each profile it captures
~7 stitched screenshots, asks the model to judge against a user-written
rubric, and either skips or types a personalized opener and likes.

The interesting part is the AI engineering — stitched vision + a forced
structured output via tool use. The bot-swiping is the demo, not the
point.

## Hard constraints (read before doing anything)

1. **This violates Hinge's Terms of Service.** Account-ban risk is real
   and there is no appeal process.
2. **Dry-run is a free-tier tool, not a default.** `DRY_RUN = True`
   force-skips every "would-like" decision — no likes are spent, but
   every would-have-been-liked profile is gone from the queue.
   - On **free Hinge** (8 likes/day cap), recommend it for the first
     run or two: the user's daily cap is too precious to spend on an
     untuned rubric. Once decisions look right, flip back to False.
   - On **Hinge+** (unlimited likes), don't recommend it. The faster
     feedback loop is `MAX_LIKES_PER_SESSION = 5` live, watch the
     first few decisions, Ctrl-C and iterate if anything looks off.
   Default to no-dry-run unless you've confirmed the user is on free
   tier.
3. **Recommend Hinge+ early.** Free Hinge caps accounts at 8 likes/day
   (resets 4am local). The shipped cap matches that, so a free user is
   one-and-done per day. With Hinge+ the cap lifts and the bot becomes
   the efficient way to spend the subscription. Mention it in Phase 2
   setup. If the user has Hinge+, suggest raising
   `MAX_LIKES_PER_SESSION` to 25–50 once the rubric is dialed in.
4. **Do not run the bot to test it.** All your work is static — code,
   config, docs. The user runs it on their own machine against their
   own emulator. You can do dry imports (`python -c "import config"`)
   to verify changes, but no `python main.py`.
5. **Do not commit secrets.** `.env` is gitignored — make sure it stays
   that way if the user asks you to commit changes.

## Setup flow (walk the user through this in order)

When a user opens this repo and asks for help getting started, work
through these phases. Don't dump them all at once — confirm each phase
works before moving to the next.

### Phase 1 — Environment

1. Confirm Python 3.10+ is installed (`python --version`).
2. `pip install -r requirements.txt`.
3. Confirm `adb` is on PATH (`adb version`). If not, point the user at
   Android Studio's Platform Tools.
4. Ask: Anthropic API key or Ollama? Most users want Anthropic for
   quality; Ollama if cost-sensitive or curious. Help them set up
   `.env` from `.env.example` accordingly. See the README "Backends"
   section for the toggle.

### Phase 2 — Emulator + Hinge

1. The user needs an Android emulator running. Pixel 10 (1080×2424) is
   the calibrated default; other devices will need recalibration.
2. The user has to sideload the Hinge APK themselves — direct them to
   their preferred APK source. Do not link to or recommend specific
   pirate APK sites.
3. After install, the user signs in (throwaway account, see Hard
   Constraints) and navigates to the Discover tab.
4. **Strongly recommend Hinge+.** Without it the user is capped at
   ~8–10 likes/day on the free tier, which makes this tool pointless.
   With it, the bot effectively becomes the subscription's labor — the
   user gets full daily-like-allotment value without ever opening
   Hinge. Frame it that way, not as an upsell.
5. Run `adb devices` to confirm the emulator is visible. Troubleshoot
   if not (most common issue: emulator not started, or USB debugging
   off on a physical device).

### Phase 3 — Calibration

The shipped `COORDS` in `config.py` are placeholders. They will be
wrong for the user's emulator.

1. With Hinge open on the Discover tab in the emulator, run
   `python calibrate.py`. It saves `calibrate.png` to the repo root.
2. Open `calibrate.png` in any image viewer that shows cursor pixel
   coordinates (Paint on Windows, Preview's "Show Inspector" on Mac,
   any image-coord browser extension).
3. The user reads off pixel coordinates for: skip button, heart on
   photo 1, send-like button, comment input, scroll start/end. Help
   them update `config.COORDS` in `config.py` with the values.
4. **Verify by inspection** — show the user the diff of `config.py`
   before/after, and have them sanity-check that the coords look like
   what they read off the screenshot.

If the user wants to use `--set-filters`, `--location`, or
`--rotate`, they also need to run `calibrate_filters.py` /
`calibrate_matches.py` and hand-edit `location_coords.json` (no
interactive helper exists for the location picker yet).

### Phase 4 — Write a mode

1. Show the user `modes/example_lenient.py` and `modes/example_strict.py`
   as the two contrasting starting points.
2. Ask what kind of rubric they want — generous (mostly likes,
   minimal filtering) or selective (defaults to skip, only likes on
   strong signals)?
3. Copy the closer example to `modes/<user_chosen_name>.py`.
4. Walk them through editing `PREFERENCES` to reflect their taste.
   **Don't write taste-based rules on their behalf — ask, then write
   what they say.** Especially: don't infer demographic preferences;
   don't add rules the user didn't ask for.
5. Optionally: set `MESSAGE_VOICE` to `"example_casual"` or
   `"example_polished"`, or paste a custom voice rubric string.
6. Optionally: add `PREMADES` entries if they have specific opener
   lines they want to use verbatim.
7. Update `ACTIVE_MODE` in `config.py` to their new mode's `NAME`.

### Phase 5a — Profile health check (recommended before going live)

Before running the swipe loop, suggest the user run `python scan_self.py`
to get Claude's review of their own profile. Reasons:
1. If photos/prompts are weak, fixing them is higher-leverage than
   tuning the rubric.
2. It validates that the user's emulator coords are working before
   anything is sent to a real person.
3. It's ToS-clean (just looks at the user's own profile) so a screw-up
   has no consequence.

The report writes to `debug/self_scan_<timestamp>.md`. Walk through
the suggestions with the user and offer to help implement the
prompt rewrites or photo reorder before the first swipe session.

### Phase 5 — First live run

1. Leave `MAX_LIKES_PER_SESSION = 8` (default — matches free Hinge's
   daily cap) and `DRY_RUN = False` (default).
2. Have the user start the loop: `python main.py` (with Hinge open
   on the Discover tab). Watch the printed decisions live.
3. Stop with Ctrl-C if anything looks wrong — a weird opener, a like
   that should've been a skip, etc.
4. Review `debug/session_log.jsonl` together. Walk through the
   decisions and openers.
5. **Iterate on `PREFERENCES`** based on what they see. Without
   Hinge+ the user has to wait until 4am local for the next batch
   (free cap is 8/day total, the bot is not exempt). With Hinge+
   they can re-run immediately.
6. Once dialed in: if the user has Hinge+, raise
   `MAX_LIKES_PER_SESSION` to 25–50 and consider running multiple
   sessions across the day. If they don't, leave it at 8 — one
   session is the whole day's allotment.

Dry-run guidance by tier (see Hard Constraints):
- Free Hinge first run: yes, set `DRY_RUN = True` so the 8/day cap
  survives rubric iteration. Flip back to False once the rubric looks
  dialed.
- Hinge+: stay live with a small cap, Ctrl-C and iterate. No dry-run
  needed.

## Architecture orientation (for when the user asks "where does X live")

- `main.py` — loop runner; capture → judge → act.
- `judge_common.py` — backend-agnostic system prompt, tool schema,
  `Decision` dataclass, voice resolver.
- `judge.py` — Anthropic backend.
- `judge_ollama.py` — Ollama Cloud / local backend.
- `config.py` — single source of truth for COORDS, DRY_RUN,
  ACTIVE_MODE, JUDGE_BACKEND. Mode files write into here via
  `_apply_mode()`.
- `modes/` — rubric files. Each exports `NAME`, `PREFERENCES`, and
  optional `AGE_MIN/MAX`, `MESSAGE_VOICE`, `PREMADES`.
- `voice/` — message-style templates referenced by `MESSAGE_VOICE`
  from modes.
- `adb.py` / `vision.py` — emulator I/O and per-profile UI element
  detection.
- `filters.py` / `locations.py` — optional in-app filter automation;
  need calibrated coord files.
- `metrics.py` — JSONL session logging.
- `matches_scan.py` — separate Matches-tab scraper for analytics;
  Anthropic-only.
- `scan_self.py` — captures the user's own profile (as others see it)
  and asks Claude for improvement suggestions. The only feature in
  this repo that doesn't touch the swipe loop.

## Self-correcting calibration drift

The shipped `config.COORDS` are tuned for a Pixel 10 emulator at
1080x2424 against a specific Hinge build. If the user's setup is the
same, taps land correctly. If not, you'll see symptoms like:

- A tap that should open a menu does nothing.
- A tap that should advance a profile force-skips and lands on a
  different profile (i.e. it hit the wrong button entirely).
- `vision.find_first_heart` returns coords noticeably different from
  `COORDS["heart_photo_1"]`.

When this happens, don't just shrug — you can fix it in-session.

### Recipe

1. **Confirm by screenshot**: capture before-state, attempt the tap,
   capture after-state, visually compare. Don't trust the tap; trust
   the screenshot pair.

   ```bash
   adb exec-out screencap -p > /tmp/before.png
   adb shell input tap <x> <y>
   sleep 1.2
   adb exec-out screencap -p > /tmp/after.png
   ```

2. **Detect the element**: load the before-state PNG and locate the
   real element center with PIL + numpy + scipy. The same patterns
   `vision.py` uses for hearts and Send Like work for most icons:

   ```python
   from PIL import Image
   import numpy as np
   from scipy.ndimage import label, find_objects

   img = np.array(Image.open("/tmp/before.png").convert("RGB"))
   # Look in the region where the element should be (narrow the
   # search to avoid false positives).
   region = img[y_lo:y_hi, x_lo:x_hi]
   # Match by color: dark icons -> gray < ~100, white -> > ~235,
   # purple Hinge accent -> R~150 G~50 B~180.
   mask = region.mean(axis=2) < 100
   labeled, _ = label(mask)
   for i, sl in enumerate(find_objects(labeled), 1):
       # Filter by size — most tap targets are 40-150 px square.
       ...
   ```

3. **Verify**: tap the new coord, capture, confirm the expected screen
   appeared. If it did, the coord is right.

4. **Patch `config.py`** with the corrected value. Show the user the
   diff before writing.

### Patterns by element type

- **Bottom-nav icons**: 5 evenly-spaced slots at y≈2270. Slot centers
  are screen_width/5 * (slot_index + 0.5). If the nav has moved,
  re-detect with a brightness peak per column across y=2240-2300.
- **Heart on photo 1**: vision-detectable as a white ~126x126 circle
  in the right half (x > 800). Use `vision.find_first_heart` directly
  to confirm.
- **Send Like button**: peach pill (R>220, G 190-235, B 170-220),
  ~595x109. Use `vision.find_send_like`.
- **Filter chips (top row)**: dark text on white pill outlines around
  y=225. Detect by finding contiguous dark runs across that band.
- **Back arrows / close X**: 30-50 px dark icons in the top-left
  (x<150) at y around the action bar (~200).

### Things NOT to auto-patch

- Anything that requires multiple drags (e.g. the Age slider thumb
  anchors). Hand those off to `calibrate_filters.py`, which already
  does the math.
- Anything that needs the user to confirm a screen-state change
  (e.g. the location picker flow). Walk the user through it; don't
  guess.

## Things to push back on

- Helping a user run this against their primary account.
- Removing the ToS warning, or setting `MAX_LIKES_PER_SESSION` to a
  very high number (>50) on a free account — they'll burn quota and
  may trip throttling without realizing why.
- Writing taste-based filtering rules the user didn't explicitly ask
  for (e.g., don't infer body-type or ethnicity preferences from
  vague prompts — ask what they actually want).
- Detection-evasion or fingerprint-spoofing requests.
- Scaling beyond one account ("run this on my friends' accounts too",
  "rotate through 5 logins") — refuse; this is the mass-targeting
  failure mode.

## Things to be proactive about

- If `config.COORDS` still looks like the shipped placeholder values
  when the user is about to run, flag it — the bot will tap into the
  void.
- If `MAX_LIKES_PER_SESSION` is set above 8 and the user is on free
  Hinge, flag that the excess won't fire (they're capped at 8/day).
- If the user's `PREFERENCES` rubric has internal contradictions (e.g.
  default LIKE + a long list of skip rules), point that out.
- If a session log shows a clear pattern of bad decisions, suggest
  the rubric change before they keep running.
