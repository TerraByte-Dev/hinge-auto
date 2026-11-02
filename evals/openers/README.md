# Opener eval

Offline harness for comparing the legacy opener prompt (`legacy_prompt.py`,
frozen from commit f0d45c4) with the new opener pipeline (facts + candidates
from the vision call, deterministic filter, a text-only select/rewrite
stage: one or two text calls per like, at most one rewrite; `OPENER_RETRY`
above 1 has no effect). Nothing here touches a phone or Hinge.

## What it measures, and what it doesn't

It renders 27 fictional profiles (invented names, generic places, no real
people) to Hinge-like PNG frames, asks a model for a decision and opener on
each, and scores the openers with text heuristics.

- Scores are heuristics and simulated model judgments on fictional
  profiles. They are **not match rates**, and nothing here shows that the new
  pipeline improves matching.
- The eval forces "like every profile" so both pipelines face the same
  opener task each time. It says nothing about taste or skip decisions.
- Prompt targeting and the photo-1 fallback are not exercised (no device);
  the report only predicts how often the fallback would apply.
- CLI numbers (`claude-cli`, `codex-cli`) are a **proxy** for the live
  Anthropic path, not a measurement of it: no forced tool call, free-text
  JSON extracted from the reply, and a different harness. Each CLI process
  runs in a fresh empty temp directory (never the repo, so no repo
  `CLAUDE.md`/`AGENTS.md` leaks in); `claude` gets the system prompt via
  `--system-prompt` and defaults to `--model sonnet`. The model each run
  requested is recorded per row and shown in the report header.
- Ollama runs may show lower comment coverage: open models often omit the
  new fields, which fails safe (a like with no comment).

## Profile tags

`profiles.json` tags: `sparse` (little to work with; empty is acceptable),
`ambiguous_image` (photos easy to misread), `serious`, `playful`, `mixed`.
Each profile also lists `expected_absent` words a hallucinating opener
might use; any hit counts against the run.

## Setup: a CLI backend (uses your subscription, no API key)

Install and sign in yourself; this repo never handles credentials.

- claude: `curl -fsSL https://claude.ai/install.sh | bash`, then run
  `claude` once and sign in.
- codex: `npm i -g @openai/codex`, then `codex login`.

Backends: `claude-cli`, `codex-cli` (subscription usage), `ollama`
(local or Ollama Cloud), `anthropic` (API key in `.env`; **costs money**).
Per-profile cost for the new pipeline is not measured (it adds one or two
text calls per like, and stage-1 output is larger). Earlier live runs were
about $0.04/profile for the vision call alone.

## Running

Run from the repo root. No-model checks first:

```
.venv/bin/python -m evals.openers.render --all            # frames for all 27 profiles
.venv/bin/python -m evals.openers.render --id <id> --out DIR
```

3-profile smoke run per pipeline (swap `claude-cli` for `codex-cli`):

```
.venv/bin/python -m evals.openers.run --backend claude-cli --pipeline legacy --tags mixed --limit 3
.venv/bin/python -m evals.openers.run --backend claude-cli --pipeline new --tags mixed --limit 3
```

Inspect the rows, then the full runs (`--tags sparse,playful` filters;
`--out PATH` sets the file; `--model` overrides the model):

```
.venv/bin/python -m evals.openers.run --backend claude-cli --pipeline legacy --out evals/openers/results/legacy.jsonl
.venv/bin/python -m evals.openers.run --backend claude-cli --pipeline new --out evals/openers/results/new.jsonl
.venv/bin/python -m evals.openers.report evals/openers/results/legacy.jsonl evals/openers/results/new.jsonl --out evals/openers/results/report
```

`report` writes `report.md` and `report.html`. Everything under `results/`
(JSONL, frames, reports) is gitignored except `.gitkeep`. A `--tags` run
covers only some groups, so the coverage items for absent groups (e.g.
`sparse` in a `--tags mixed` run) show as fail: there are no likes there to
count.

## Reading the report

1. **Coverage first.** Share of likes with a non-empty opener, overall and
   per tag, plus why openers were empty (no facts, no candidates, all
   filtered, stage-2 rejected or errored). An empty comment is the intended
   outcome when nothing passes the checks, but a run that is mostly empty
   fails.
2. **Quality, on non-empty openers only:** question-ending rate, median
   length, banned phrases, grounding in the profile, repeated openings,
   hallucination flags (`expected_absent`, invented shared experience).
3. **Acceptance targets** (spec §7, heuristic): no error or skip rows (the
   eval forces likes, so an error or skip would hide a profile from the
   coverage rates; counts are shown); coverage >= 85% on non-`sparse` and
   >= 50% on `sparse`; new no more than 10 points below legacy on any tag;
   zero `expected_absent` hits and zero filter-detectable violations among
   sent openers (by construction for the new pipeline, which runs the same
   filter; meaningful for legacy); question-ending rate below legacy's.
   Failures are reported, not hidden.
