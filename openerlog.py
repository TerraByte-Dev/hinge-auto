"""Opener review log: one record per liked profile, across all runs.

`debug/openers.jsonl` (gitignored, append-only, never truncated) keeps what
the judge read off the profile (`profile_facts`), every stage-1 opener
suggestion with its filter verdict, each stage-2 call's choice and reason,
the final text, and whether it actually went out (`opener_sent`: a confirmed
like with a non-empty comment). `prompt_hash` / `git_commit` / `mode` tell
records apart across prompt and voice changes.

  python -m openerlog [last|all|<run_id>] [--sent-only] [--mode M]
                     [--html | --watch [SECONDS]]

`--html` writes debug/openers.html: one card per profile with its saved
frames (relative links into debug/liked/), so it stays a local file.
`--watch` adds a reload tag. The loop also rewrites the page (all runs)
after every like, reloading every config.OPENERLOG_HTML_REFRESH seconds,
so an open tab follows a live run.
Records hold other people's profile details - they stay under debug/.
"""

import argparse
import collections
import functools
import hashlib
import html
import json
import sys
from datetime import datetime
from pathlib import Path

import config
import runlog

SCHEMA = 1


def log_path() -> Path:
    return config.DEBUG_DIR / "openers.jsonl"


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    return runlog._git_commit()


def _prompt_hash() -> str | None:
    """Short hash of the judge system prompt + stage-2 rubric, so records
    written under different prompts / voices / modes can be told apart."""
    try:
        import judge_common
        import opener
        text = judge_common.build_system_prompt() + opener.SELECT_SYSTEM
    except Exception:
        return None
    return hashlib.sha256(text.encode()).hexdigest()[:10]


def record(run_id: str | None, profile: int, decision, status: str,
           outcome: str, frames_dir: Path | None) -> None:
    """Append one record for a like decision; skips write nothing."""
    if decision.decision != "like":
        return
    trace = getattr(decision, "opener_trace", None) or {}
    final = decision.message or ""
    if frames_dir is not None:
        try:
            frames_dir = Path(frames_dir).relative_to(config.DEBUG_DIR).as_posix()
        except ValueError:
            frames_dir = str(frames_dir)
    rec = {
        "schema": SCHEMA,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "profile": profile,
        "name": decision.name,
        "mode": config.MODE_NAME,
        "voice": config.MESSAGE_VOICE,
        "prompt_hash": _prompt_hash(),
        "git_commit": _git_commit(),
        "dry_run": config.DRY_RUN,
        "confidence": getattr(decision, "confidence", None),
        "profile_facts": getattr(decision, "profile_facts", None) or [],
        "candidates": trace.get("candidates", []),
        "stage2": trace.get("stage2", []),
        "status": status,
        "premade_id": getattr(decision, "premade_id", None) or "",
        "final": final,
        "archetype": getattr(decision, "message_archetype", None),
        "prompt_referenced": getattr(decision, "prompt_referenced", None),
        "outcome": outcome,
        "opener_sent": outcome in runlog.LIKE_OUTCOMES and bool(final),
        "frames_dir": frames_dir,
    }
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = runlog.redact(json.dumps(rec, ensure_ascii=False, default=str))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- reading

def load(path: Path | None = None) -> list[dict]:
    """All records, oldest first. Unparseable lines (a crash mid-write) are
    skipped; missing fields are left to the renderers."""
    path = path or log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def select(recs: list[dict], which: str = "last", sent_only: bool = False,
           mode: str | None = None) -> list[dict]:
    if which == "last":
        which = recs[-1].get("run_id") if recs else None
    if which != "all":
        recs = [r for r in recs if r.get("run_id") == which]
    if sent_only:
        recs = [r for r in recs if r.get("opener_sent")]
    if mode:
        recs = [r for r in recs if r.get("mode") == mode]
    return recs


def _fact_line(f: dict) -> str:
    head = f"{f.get('id', '?')} {f.get('kind', '?')} {f.get('index', '')}".strip()
    parts = []
    if f.get("prompt_text"):
        parts.append(f'"{f["prompt_text"]}"')
    if f.get("answer_text"):
        parts.append(f"-> {f['answer_text']}")
    if f.get("detail"):
        parts.append(f"({f['detail']})" if parts else str(f["detail"]))
    return f"{head:<14} {' '.join(parts)}"


def _rewrites(r: dict) -> list[dict]:
    return [s["rewrite"] for s in r.get("stage2") or []
            if isinstance(s, dict) and isinstance(s.get("rewrite"), dict)]


def _header(r: dict) -> str:
    sent = "SENT" if r.get("opener_sent") else "not sent"
    return (f"run {r.get('run_id', '?')}  profile {r.get('profile', '?')}  "
            f"{r.get('name', '?')}  [{r.get('mode') or '-'}]  "
            f"outcome={r.get('outcome') or '-'}  status={r.get('status') or '-'}  {sent}")


def render_text(recs: list[dict]) -> str:
    """✓/✗ = filter verdict, ▸ = stage 1's pick, ★ = the final text."""
    out = []
    for r in recs:
        final = r.get("final") or ""
        out.append("=" * 72)
        out.append(_header(r))
        facts = r.get("profile_facts") or []
        out.append("facts:" if facts else "facts: —")
        out += [f"  {_fact_line(f)}" for f in facts if isinstance(f, dict)]
        cands = r.get("candidates") or []
        out.append("candidates:" if cands else "candidates: —")
        for i, c in enumerate(cands):
            mark = "✗" if c.get("reject") else "✓"
            pick = "▸" if c.get("stage1_pick") else " "
            star = "★" if final and c.get("text") == final else " "
            why = f"  — {', '.join(c['reject'])}" if c.get("reject") else ""
            out.append(f"  {i} {mark}{pick}{star} [{c.get('form')} {c.get('fact_id')}] "
                       f"{c.get('text')}{why}")
        for s in r.get("stage2") or []:
            if not isinstance(s, dict):
                continue
            if "error" in s:
                out.append(f"stage 2: error {s['error']}")
                continue
            out.append(f"stage 2: choice {s.get('choice_index')} — {s.get('reason') or ''}")
            rw = s.get("rewrite")
            if isinstance(rw, dict):
                star = "★" if final and rw.get("text") == final else " "
                why = f"  — {', '.join(s['rewrite_reject'])}" if s.get("rewrite_reject") else ""
                out.append(f"   rewrite {star} [{rw.get('form')} {rw.get('fact_id')}] "
                           f"{rw.get('text')}{why}")
        if r.get("premade_id"):
            out.append(f"premade: {r['premade_id']}")
        out.append(f"final: {final!r}" if final else "final: (no comment)")
    return "\n".join(out)


def summary(recs: list[dict]) -> str:
    sent = sum(1 for r in recs if r.get("opener_sent"))
    statuses = collections.Counter(r.get("status") or "-" for r in recs)
    rejects = collections.Counter(code for r in recs for c in r.get("candidates") or []
                                  for code in c.get("reject") or [])
    picked = overrode = 0
    for r in recs:
        final = r.get("final")
        pick = next((c.get("text") for c in r.get("candidates") or []
                     if c.get("stage1_pick")), None)
        if final and pick:
            picked += 1
            overrode += final != pick
    lines = [
        f"{len(recs)} likes | {sent} opener sent | "
        + " ".join(f"{k}={v}" for k, v in sorted(statuses.items())),
        f"stage 2 overrode stage 1's pick: {overrode}/{picked}",
    ]
    if rejects:
        lines.append("top rejects: " + " ".join(f"{k}={v}" for k, v in rejects.most_common(8)))
    return "\n".join(lines)


_CSS = """
:root { --bg:#fafaf9; --card:#fff; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4;
  --ok:#15803d; --bad:#b91c1c; --hi:#fef3c7; }
@media (prefers-color-scheme: dark) { :root { --bg:#1c1917; --card:#292524;
  --fg:#f5f5f4; --muted:#a8a29e; --line:#44403c; --ok:#4ade80; --bad:#f87171; --hi:#422006; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 -apple-system, system-ui, sans-serif;
  margin:0 auto; padding:16px; max-width:1100px; }
h1 { font-size:20px; } pre.sum { color:var(--muted); white-space:pre-wrap; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:14px; margin:14px 0; }
.head { font-weight:600; } .meta { color:var(--muted); font-size:12px; }
.frames { display:flex; gap:6px; overflow-x:auto; margin:10px 0; }
.frames img { height:260px; border-radius:6px; border:1px solid var(--line); }
ul { margin:4px 0; padding-left:20px; } li { margin:2px 0; }
.ok { color:var(--ok); } .bad { color:var(--bad); } .final { background:var(--hi);
  border-radius:4px; padding:0 3px; font-weight:600; }
.sent { color:var(--ok); font-weight:600; } .unsent { color:var(--muted); }
"""


# Keeps the reader's scroll position across meta-refresh reloads.
_KEEP_SCROLL = """<script>
try { addEventListener("beforeunload", () =>
        sessionStorage.setItem("openers_y", String(scrollY)));
      addEventListener("load", () =>
        scrollTo(0, +(sessionStorage.getItem("openers_y") || 0))); } catch (e) {}
</script>"""


def render_html(recs: list[dict], debug_dir: Path, refresh: int | None = None) -> str:
    """`refresh` > 0 adds a meta reload every that many seconds."""
    e = html.escape
    cards = []
    for r in reversed(recs):  # newest first
        final = r.get("final") or ""
        frames = ""
        fd = r.get("frames_dir")
        if fd and (debug_dir / fd).is_dir():
            imgs = sorted((debug_dir / fd).glob("*.png"))
            frames = '<div class="frames">' + "".join(
                f'<img loading="lazy" src="{e(Path(fd, p.name).as_posix())}">'
                for p in imgs) + "</div>"
        facts = "".join(f"<li>{e(_fact_line(f))}</li>"
                        for f in r.get("profile_facts") or [] if isinstance(f, dict))
        cands = []
        for c in r.get("candidates") or []:
            cls = "bad" if c.get("reject") else "ok"
            mark = "✗" if c.get("reject") else "✓"
            text = e(str(c.get("text")))
            if final and c.get("text") == final:
                text = f'<span class="final">{text}</span>'
            pick = " ▸ stage-1 pick" if c.get("stage1_pick") else ""
            why = f" — {e(', '.join(c['reject']))}" if c.get("reject") else ""
            cands.append(f'<li><span class="{cls}">{mark}</span> '
                         f'<span class="meta">[{e(str(c.get("form")))} {e(str(c.get("fact_id")))}]</span> '
                         f'{text}<span class="meta">{pick}{e(why)}</span></li>')
        stage2 = []
        for s in r.get("stage2") or []:
            if not isinstance(s, dict):
                continue
            if "error" in s:
                stage2.append(f"<li class='bad'>error: {e(str(s['error']))}</li>")
                continue
            line = f"choice {e(str(s.get('choice_index')))} — {e(str(s.get('reason') or ''))}"
            rw = s.get("rewrite")
            if isinstance(rw, dict):
                t = e(str(rw.get("text")))
                if final and rw.get("text") == final:
                    t = f'<span class="final">{t}</span>'
                line += f"<br>rewrite: {t}"
            stage2.append(f"<li>{line}</li>")
        sent = ('<span class="sent">SENT</span>' if r.get("opener_sent")
                else '<span class="unsent">not sent</span>')
        cards.append(f"""<div class="card">
<div class="head">{e(str(r.get('name', '?')))} · {sent}</div>
<div class="meta">run {e(str(r.get('run_id')))} · profile {e(str(r.get('profile')))} ·
mode {e(str(r.get('mode')))} · status {e(str(r.get('status')))} · outcome {e(str(r.get('outcome')))}
· prompt {e(str(r.get('prompt_hash')))} · {e(str(r.get('ts', '')))}</div>
{frames}
<b>Profile facts</b><ul>{facts or '<li>—</li>'}</ul>
<b>Suggestions</b><ul>{''.join(cands) or '<li>—</li>'}</ul>
{'<b>Stage 2</b><ul>' + ''.join(stage2) + '</ul>' if stage2 else ''}
<b>Final:</b> {f'<span class="final">{e(final)}</span>' if final else '(no comment)'}
</div>""")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{f'<meta http-equiv="refresh" content="{int(refresh)}">' + _KEEP_SCROLL if refresh else ''}
<title>Opener Review</title><style>{_CSS}</style></head><body>
<h1>Opener review</h1><pre class="sum">{e(summary(recs))}</pre>
{''.join(cards)}
</body></html>"""


def write_html(recs: list[dict] | None = None, refresh: int | None = None) -> Path:
    """Write debug/openers.html (default: every record, reload interval from
    config.OPENERLOG_HTML_REFRESH). Written to a temp file and renamed, so a
    reload mid-write never shows half a page."""
    if recs is None:
        recs = load()
    if refresh is None:
        refresh = getattr(config, "OPENERLOG_HTML_REFRESH", 0)
    out = config.DEBUG_DIR / "openers.html"
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(render_html(recs, config.DEBUG_DIR, refresh), encoding="utf-8")
    tmp.replace(out)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m openerlog",
                                description="Review recorded openers.")
    p.add_argument("which", nargs="?", default="last", help="last | all | <run_id>")
    p.add_argument("--sent-only", action="store_true")
    p.add_argument("--mode")
    p.add_argument("--html", action="store_true",
                   help="write debug/openers.html instead of printing")
    p.add_argument("--watch", type=int, nargs="?", const=30, metavar="SECONDS",
                   help="like --html, reloading every SECONDS (default 30)")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    recs = select(load(), args.which, args.sent_only, args.mode)
    if not recs:
        print(f"no opener records for '{args.which}' in {log_path()}")
        return 1
    if args.html or args.watch:
        out = write_html(recs, args.watch or 0)
        print(f"{len(recs)} records -> {out}")
        return 0
    print(render_text(recs))
    print("=" * 72)
    print(summary(recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
