"""Offline opener eval runner: render each fictional profile, judge it with
the legacy or new opener pipeline, write one JSONL row per profile.

    python -m evals.openers.run --backend claude-cli --pipeline new
    python -m evals.openers.run --backend codex-cli --pipeline legacy --limit 3
    python -m evals.openers.run --backend anthropic --pipeline new --tags sparse,serious

Preferences are fixed to "like every profile" so both pipelines face the
same opener task. `claude-cli` / `codex-cli` run on your subscription;
`anthropic` uses the API key in .env (costs money); `ollama` uses your
local/cloud Ollama model. Results are heuristic, not match rates.
"""
import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config
import opener
from judge_common import (
    DECIDE_INPUT_SCHEMA,
    build_system_prompt,
    decision_from_dict,
    enforce_premade_verbatim,
)
from evals.openers import legacy_prompt
from evals.openers.render import DEFAULT_OUT, load_profiles, render_profile

HERE = Path(__file__).parent
RESULTS = HERE / "results"
FRAMES_ROOT = DEFAULT_OUT
BACKENDS = ("anthropic", "ollama", "claude-cli", "codex-cli")
TOOL_NAME = "submit_decision"

EVAL_CONFIG = {
    "PREFERENCES": "Decision: like every profile.",
    "AGE_MIN": None,
    "AGE_MAX": None,
    "MESSAGE_VOICE": None,
}


class _ApiBackend:
    """Adapts judge.py / judge_ollama.py (PNG bytes) to the path-based API."""

    def __init__(self, module):
        self.module = module

    def vision_json(self, system, frames, schema, name):
        return self.module.vision_json(system, [Path(f).read_bytes() for f in frames],
                                       schema, name)

    def complete_json(self, system, user, schema, name):
        return self.module.complete_json(system, user, schema, name)


def make_backend(name: str, model: str | None):
    if name == "anthropic":
        from dotenv import load_dotenv
        import judge
        load_dotenv()
        if model:
            config.MODEL = model
        return _ApiBackend(judge)
    if name == "ollama":
        import judge_ollama
        if model:
            config.OLLAMA_MODEL = model
        return _ApiBackend(judge_ollama)
    from evals.openers.cli_backends import ClaudeCli, CodexCli
    if name == "claude-cli":
        return ClaudeCli(model)
    if name == "codex-cli":
        return CodexCli(model)
    raise ValueError(f"unknown backend {name!r}")


def default_out(backend: str, pipeline: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RESULTS / f"{ts}-{backend}-{pipeline}.jsonl"


def _legacy_status(d) -> str:
    if d.decision != "like":
        return "skip"
    if not d.message.strip():
        return "empty_legacy"
    return "premade" if d.message_archetype == "premade" else "legacy_sent"


def judge_profile(p: dict, frames: list[Path], backend, pipeline: str,
                  history) -> dict:
    """Row fields from one judged profile (raises on backend failure)."""
    if pipeline == "legacy":
        system, schema = legacy_prompt.SYSTEM_PROMPT, legacy_prompt.SCHEMA
    else:
        system, schema = build_system_prompt(), DECIDE_INPUT_SCHEMA
    out = backend.vision_json(system, frames, schema, TOOL_NAME)
    usage = out.pop("_usage", None) or {}
    d = decision_from_dict(out, dict(usage))

    if pipeline == "legacy":
        # Taken as-is: the old loop sent whatever message the model wrote.
        status = _legacy_status(d)
        label = d.prompt_referenced or ""
        anchor_kind = "unknown" if d.message.strip() else ""
        n_facts = n_cands = 0
    else:
        n_facts, n_cands = len(d.profile_facts), len(d.candidates)
        enforce_premade_verbatim(d)
        status = opener.finalize(d, backend.complete_json, history)
        anchor = d.anchor if isinstance(d.anchor, dict) else {}
        anchor_kind = str(anchor.get("kind") or "")
        label = d.prompt_referenced or ""

    return {
        "decision": dataclasses.asdict(d),
        "message": d.message,
        "form": d.message_archetype,
        "anchor_kind": anchor_kind,
        "anchor_label": label,
        "status": status,
        "n_facts": n_facts,
        "n_candidates": n_cands,
        "usage": d.usage,
    }


def _select(profiles: list[dict], tags: str | None, limit: int | None) -> list[dict]:
    if tags:
        want = {t.strip() for t in tags.split(",") if t.strip()}
        profiles = [p for p in profiles if want & set(p.get("tags", []))]
    if limit is not None:
        profiles = profiles[:limit]
    return profiles


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=BACKENDS, required=True)
    ap.add_argument("--pipeline", choices=("legacy", "new"), required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tags", help="comma-separated; keep profiles with any of them")
    ap.add_argument("--model", help="model override for the chosen backend")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    out = args.out or default_out(args.backend, args.pipeline)
    profiles = _select(load_profiles(), args.tags, args.limit)

    if args.backend == "anthropic":
        print(f"WARNING: --backend anthropic uses the API key in .env and costs "
              f"money ({len(profiles)} profiles, two calls per like).", file=sys.stderr)
    elif args.backend == "ollama":
        print("WARNING: --backend ollama uses your configured Ollama model "
              "(local, or Ollama Cloud if OLLAMA_HOST/OLLAMA_API_KEY are set).",
              file=sys.stderr)

    saved = {k: getattr(config, k) for k in (*EVAL_CONFIG, "PREMADES", "MODEL",
                                             "OLLAMA_MODEL")}
    try:
        for k, v in EVAL_CONFIG.items():
            setattr(config, k, v)
        config.PREMADES = []  # legacy carries its own frozen premade section
        backend = make_backend(args.backend, args.model)
        history = opener.OpenerHistory()
        frames_root = FRAMES_ROOT / out.stem
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for p in profiles:
                row = {"profile_id": p["id"], "tags": list(p.get("tags", [])),
                       "pipeline": args.pipeline, "backend": args.backend}
                t0 = time.monotonic()
                try:
                    frames = render_profile(p, frames_root / p["id"])
                    row.update(judge_profile(p, frames, backend, args.pipeline, history))
                    row["error"] = None
                except Exception as e:  # noqa: BLE001 -- record and continue
                    row.update({"decision": None, "message": "", "form": "",
                                "anchor_kind": "", "anchor_label": "",
                                "status": "error", "n_facts": 0, "n_candidates": 0,
                                "usage": {}, "error": f"{type(e).__name__}: {e}"})
                row["seconds"] = round(time.monotonic() - t0, 2)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                shown = row["error"] if row["status"] == "error" else row["message"]
                print(f"{p['id']}: {row['status']} {shown!r}", flush=True)
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
