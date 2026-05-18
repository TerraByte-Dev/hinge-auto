# HingeAuto

> ## Read this first
>
> **This project automates Hinge, which violates Hinge's Terms of Service.**
> Real risk of account ban with no appeal. Treat this as an educational toy
> for a single throwaway account, not a dating strategy.
>
> - One account only. Low volume. **Keep `DRY_RUN = True` until you have
>   watched a full session end-to-end and confirmed the decisions look sane.**
> - No warranty. No support. Your account, your problem.
> - The repo exists because automating a stitched-vision + LLM-judge loop
>   on a phone UI is an interesting AI engineering exercise, not because
>   anyone thinks bot-swiping is good dating advice.

## Tip: open this repo in Claude Code or Codex

This repo ships with [`AGENTS.md`](./AGENTS.md) (and [`CLAUDE.md`](./CLAUDE.md)).
If you cloned this and you're not sure where to start, open the
directory in Claude Code, Codex CLI, Cursor, or any agent that
respects `AGENTS.md` and say _"help me set this up"_. The agent will
walk you through emulator config, calibration, writing a rubric, and
the first dry run.

If you'd rather do it manually, read on.

## What it does

Drives an Android emulator running Hinge through ADB. For each profile it
scrolls top-to-bottom, screenshots the frames, asks Claude to judge against
a user-defined rubric, and either taps Skip or taps the heart and types a
personalized opener.

## Quickstart

1. Install [Android Studio](https://developer.android.com/studio) and
   create an emulator. Pixel 10 (1080×2424) is the default target — other
   profiles will need re-calibration.
2. Sideload the Hinge APK into the emulator, sign in, get to the Discover
   tab. Make sure `adb devices` lists it.
3. Clone this repo and install deps:
   ```
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and add your Anthropic API key.
5. Run calibration — this saves a screenshot you read pixel coords from:
   ```
   python calibrate.py
   ```
   Open the resulting `calibrate.png` in any image viewer that shows cursor
   coordinates (Paint, IrfanView, Preview's tool inspector) and edit
   `config.py` `COORDS` to match.
6. Run the loop:
   ```
   python main.py
   ```
   The default config has `DRY_RUN = True` — it captures, judges, logs,
   and force-skips so nothing is sent. Watch a few profiles. When you're
   sure the decisions match your rubric, flip `DRY_RUN = False` in
   `config.py`.

## Writing your own mode

Two example rubrics ship in `modes/`: `example_lenient.py` (default-LIKE)
and `example_strict.py` (default-SKIP). Use them as starting points.

- Copy `modes/template.py.example` to `modes/<your_name>.py`.
- Edit `PREFERENCES` to describe what should and shouldn't get a like.
- Optionally set `MESSAGE_VOICE` to one of the templates under `voice/`
  (e.g. `"example_casual"` or `"example_polished"`), or paste a multi-line
  rubric string directly.
- Optionally populate `PREMADES` with verbatim openers Claude can pick
  from instead of writing fresh copy.
- Point `ACTIVE_MODE` in `config.py` at the new file, or pass
  `--mode <your_name>` on the command line.

## Calibration

ADB drives the emulator by tapping absolute pixel coordinates, so anything
that moves a UI element invalidates the saved coords.

- `python calibrate.py` — main coords (skip / heart / scroll). Run once
  after your first emulator setup, then again any time Hinge updates the
  Discover layout.
- `python calibrate_filters.py` — Age filter slider coords. Only needed
  if you plan to use `python main.py --set-filters` to drive the in-app
  age range.
- `python calibrate_matches.py` — Matches-tab tap target. Only needed if
  you run `matches_scan.py` to scrape your conversations list.

The location picker (`locations.py`) needs a `location_coords.json` file.
A schema is provided at `location_coords.json.example` — copy it, rename,
and fill in real pixel coords by hand from a `calibrate.py` screenshot.
(The original project had a `calibrate_locations.py` interactive helper
but it was never written; PRs welcome.)

## Architecture

```
ADB capture  →  frame stitching  →  Claude judge  →  action
   adb.py        config / main         judge.py       main.py
                                       vision.py      adb.py
```

- **`adb.py`** wraps the `adb` CLI: screenshot, tap, swipe, type.
- **`main.py`** is the loop. For each profile: scroll-to-top, capture
  N frames, run them through `judge.judge()`, then either skip or scroll
  back, tap the heart, type the opener, and tap Send Like.
- **`judge.py`** sends the frames + the active mode's rubric to Claude
  and parses a structured `Decision` out of a forced tool call. Caches
  the system prompt to keep cost down.
- **`vision.py`** finds UI elements whose absolute position shifts
  per-profile (heart icon on photo 1, Send Like button, comment input).
  Pixel-level detection, not OCR.
- **`modes/`** holds rubric files. `config._apply_mode()` copies the
  active mode's `PREFERENCES`, `AGE_MIN/MAX`, `MESSAGE_VOICE`, and
  `PREMADES` onto the `config` module so the rest of the code reads
  them via `config.<name>`.
- **`voice/`** holds opener-tone templates that modes can reference by
  name.
- **`metrics.py`** appends one JSONL record per profile to
  `debug/session_log.jsonl` for after-the-fact analysis.
- **`filters.py` / `locations.py`** drive Hinge's in-app filter sheets
  (age, neighborhood). Both need calibrated coord files.
- **`matches_scan.py`** scrapes the Matches tab via a separate Claude
  vision pass — for analytics, not for the swipe loop.

## Safety and rate limits

- Keep `DRY_RUN = True` for your first runs.
- `MAX_LIKES_PER_SESSION = 25` is the shipped default. Going higher
  tends to trigger Hinge's soft-throttle (empty Discover after a burst).
- Don't change locations more than ~2 times per day. Frequent MyMove
  changes get throttled.
- One account. Don't run this on your real Hinge account.
- Anthropic API spend is roughly $0.02–$0.05 per profile on
  Sonnet at medium effort. Watch the running totals printed each loop.

## Backends: Anthropic API vs Ollama (free)

The judge pipeline ships with two interchangeable backends. Pick via
`JUDGE_BACKEND` in `config.py`.

### `"anthropic"` (default, recommended)

Uses Claude with vision + forced tool calling. Best quality decisions
and best opener writing. Roughly **$0.02–$0.05 per profile** on Sonnet
at medium effort.

Setup:
1. `pip install -r requirements.txt`
2. Put `ANTHROPIC_API_KEY=...` in `.env`.
3. Leave `JUDGE_BACKEND = "anthropic"` in `config.py`.

### `"ollama"` (free — Ollama Cloud or local)

Uses an open-weight vision model through Ollama. **No per-token cost**
on the free tier of Ollama Cloud, or fully local on your own GPU.

Setup:
1. `pip install -r requirements.txt && pip install -r requirements-ollama.txt`
2. Either:
   - **Ollama Cloud** — sign up at <https://ollama.com>, create an API
     key, set `OLLAMA_API_KEY=...` in your `.env`, and set
     `OLLAMA_HOST = "https://ollama.com"` in `config.py`.
   - **Local Ollama** — install Ollama, `ollama pull qwen2.5-vl`, run
     `ollama serve`. Leave `OLLAMA_HOST = None` (defaults to
     `http://localhost:11434`).
3. Set `JUDGE_BACKEND = "ollama"` in `config.py`.

Honest tradeoffs:
- **Decision quality** on a 7-screenshot judgment is meaningfully worse
  than Sonnet — expect more wrong skips on good profiles and more
  generic openers.
- **Tool-calling reliability** varies by model. `qwen2.5-vl` is the
  best of the open-weight options as of this writing. If you see
  `RuntimeError: Ollama (...) did not return a usable
  submit_decision call`, try a larger variant (`qwen2.5-vl:32b`) or
  switch to `llama3.2-vision`.
- The `matches_scan.py` analytics scrape still uses Anthropic — it's a
  separate tool, not the swipe loop, and the swap there isn't wired up.

Both backends share the same system prompt, schema, and `Decision`
shape (in `judge_common.py`) — only the API call differs.

## License

MIT. See `LICENSE`.

## Contributing

This is a personal project published as-is. PRs that strip more PII,
fix calibration on additional devices, or add a real
`calibrate_locations.py` are welcome. PRs that improve evasion of
Hinge's bot-detection are not.
