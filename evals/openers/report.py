"""Summarise opener-eval JSONL runs into Markdown + HTML.

    python -m evals.openers.report NEW.jsonl [LEGACY.jsonl] --out results/report

With two files, the one whose rows say pipeline "legacy" is the baseline.
Coverage comes first; quality metrics count non-empty openers only. All
scores are heuristic model and text checks on fictional profiles, not match
rates.
"""
import argparse
import html
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import opener
from evals.openers import render

DISCLAIMER = ("Scores are heuristic model and text checks (simulated judgments on "
              "fictional profiles), not match rates. Acceptance targets are "
              "heuristic, for this change only.")

# Text-level filter codes (comparable across pipelines; the fact/form/history
# checks need stage-1 data the legacy arm doesn't have).
_TEXT_ONLY_DROP = {"bad_fact", "bad_form", "ungrounded", "repeat"}
_BANNED = {code for code, _ in opener._PATTERN_RULES}
_FIRST_PERSON = re.compile(
    r"\bi (?:spent|went|did|was|used to|grew up|lived|worked|tried|have been)\b"
    r"|\bmy (?:trip|time|summer|year|semester)\b",
    re.IGNORECASE)

THRESHOLDS = {"non_sparse": 0.85, "sparse": 0.50, "max_tag_drop": 0.10}


def load_profiles() -> dict:
    return {p["id"]: p for p in render.load_profiles()}


def predicted_target(row: dict) -> str:
    """Where the live loop would tap: the prompt's heart only for prompt
    anchors, otherwise photo 1 (the fallback)."""
    return "prompt" if row.get("anchor_kind") == "prompt" else "photo1"


def _is_like(row: dict) -> bool:
    d = row.get("decision")
    return (row.get("status") != "error" and isinstance(d, dict)
            and d.get("decision") == "like")


def _msg(row: dict) -> str:
    return (row.get("message") or "").strip()


def _source_text(p: dict) -> str:
    parts = [p.get("info", "")]
    for e in p.get("elements", []):
        parts += [str(v) for k, v in e.items() if k != "kind"]
    return " ".join(parts)


def _absent_hits(msg: str, terms) -> list[str]:
    return [t for t in terms or []
            if re.search(r"\b" + re.escape(t) + r"\b", msg, re.IGNORECASE)]


def _text_violations(msg: str) -> list[str]:
    codes = opener.check_opener({"text": msg, "fact_id": None, "form": "question"},
                                [], None)
    return [c for c in codes if c not in _TEXT_ONLY_DROP]


def _rate(n, d):
    return n / d if d else 0.0


def _tags(row, profiles):
    return row.get("tags") or profiles.get(row.get("profile_id"), {}).get("tags", [])


def _counts(likes, pred) -> tuple[int, int]:
    group = [r for r in likes if pred(r)]
    return sum(bool(_msg(r)) for r in group), len(group)


def _coverage(likes, pred) -> float:
    """Non-empty share of the likes matching `pred`; 0.0 for an empty group
    (no likes is not evidence of coverage)."""
    return _rate(*_counts(likes, pred))


def summarise(rows: list[dict], profiles: dict, baseline: dict | None = None) -> dict:
    """Metrics for one run. `baseline` (a legacy summary) enables the
    relative acceptance items; without it they are "n/a"."""
    likes = [r for r in rows if _is_like(r)]
    sent = [r for r in likes if _msg(r)]
    tags = sorted({t for r in rows for t in _tags(r, profiles)})

    s = {
        "pipeline": ",".join(sorted({str(r.get("pipeline")) for r in rows})) or "?",
        "backend": ",".join(sorted({str(r.get("backend")) for r in rows})) or "?",
        "model": ",".join(sorted({str(r.get("model") or "?") for r in rows})) or "?",
        "rows": len(rows),
        "likes": len(likes),
        "skips": sum(1 for r in rows if r.get("status") != "error" and not _is_like(r)),
        "errors": sum(1 for r in rows if r.get("status") == "error"),
        "nonempty": len(sent),
        # --- coverage first
        "coverage": _rate(len(sent), len(likes)),
        "coverage_all_rows": _rate(len(sent), len(rows)),
        "coverage_by_tag": {t: _coverage(likes, lambda r, t=t: t in _tags(r, profiles))
                            for t in tags},
        "coverage_non_sparse": _coverage(likes, lambda r: "sparse" not in _tags(r, profiles)),
        "coverage_sparse": _coverage(likes, lambda r: "sparse" in _tags(r, profiles)),
        "counts_non_sparse": _counts(likes, lambda r: "sparse" not in _tags(r, profiles)),
        "counts_sparse": _counts(likes, lambda r: "sparse" in _tags(r, profiles)),
        "empty_reasons": dict(Counter(r.get("status") for r in likes if not _msg(r))),
        "premade_rate": _rate(sum(r.get("status") == "premade" for r in likes), len(likes)),
        "rewrite_rate": _rate(sum(r.get("status") == "rewritten" for r in likes), len(likes)),
        "target_mix": {k: _rate(v, len(likes)) for k, v in
                       Counter(predicted_target(r) for r in likes).items()},
    }

    # --- quality, non-empty openers only
    msgs = [_msg(r) for r in sent]
    s["question_rate"] = _rate(sum(m.endswith("?") for m in msgs), len(msgs)) if msgs else None
    s["median_length"] = statistics.median(len(m) for m in msgs) if msgs else None

    banned, violations, absent, bio = Counter(), [], [], []
    grounded = 0
    for r, m in zip(sent, msgs):
        pid = r.get("profile_id")
        p = profiles.get(pid, {})
        codes = _text_violations(m)
        banned.update(c for c in codes if c in _BANNED)
        if codes:
            violations.append({"profile_id": pid, "codes": codes, "message": m})
        for t in _absent_hits(m, p.get("expected_absent")):
            absent.append({"profile_id": pid, "term": t, "message": m})
        if opener._SHARED.search(m) or _FIRST_PERSON.search(m):
            bio.append({"profile_id": pid, "message": m})
        if p and opener._grounded(m, _source_text(p)):
            grounded += 1
    s["banned_hits"] = dict(banned)
    s["filter_violations"] = violations
    s["expected_absent_hits"] = absent
    s["sender_bio_flags"] = bio
    s["grounded_rate"] = _rate(grounded, len(msgs)) if msgs else None

    first = Counter(m.split()[0].lower().strip(",.!?") for m in msgs if m.split())
    if first:
        w, n = first.most_common(1)[0]
        s["top_opening_word"] = (w, n / len(msgs))
    else:
        s["top_opening_word"] = None
    three = Counter(opener._words3(m) for m in msgs)
    s["opening3_repeat_rate"] = (_rate(sum(three[opener._words3(m)] > 1 for m in msgs),
                                       len(msgs)) if msgs else None)
    s["anchor_kind_mix"] = dict(Counter(r.get("anchor_kind") or "none" for r in sent))
    s["form_mix"] = dict(Counter(r.get("form") or "none" for r in sent))

    s["acceptance"] = _acceptance(s, baseline)
    return s


def _item(id_, label, ok, detail):
    status = "n/a" if ok is None else ("pass" if ok else "fail")
    return {"id": id_, "label": label, "status": status, "detail": detail}


def _acceptance(s: dict, base: dict | None) -> dict:
    items = [
        # The eval forces likes, so an error or skip row is a profile whose
        # coverage went unmeasured; it can't be allowed to flatter the rates.
        _item("no_errors_or_skips", "No error or skip rows (the eval forces likes)",
              s["errors"] == 0 and s["skips"] == 0,
              f"{s['errors']} errors, {s['skips']} skips"),
        _item("coverage_non_sparse", "Non-empty coverage >= 85% on non-sparse",
              s["coverage_non_sparse"] >= THRESHOLDS["non_sparse"],
              "{:.0%} ({}/{} likes)".format(s["coverage_non_sparse"], *s["counts_non_sparse"])),
        _item("coverage_sparse", "Non-empty coverage >= 50% on sparse",
              s["coverage_sparse"] >= THRESHOLDS["sparse"],
              "{:.0%} ({}/{} likes)".format(s["coverage_sparse"], *s["counts_sparse"])),
    ]
    if base is None:
        items.append(_item("tag_drop_vs_legacy",
                           "Coverage no more than 10 points below legacy on any tag",
                           None, "needs a legacy run"))
    else:
        drops = {t: base["coverage_by_tag"].get(t, 0.0) - c
                 for t, c in s["coverage_by_tag"].items()}
        worst = max(drops.items(), key=lambda kv: kv[1], default=("-", 0.0))
        items.append(_item("tag_drop_vs_legacy",
                           "Coverage no more than 10 points below legacy on any tag",
                           all(d <= THRESHOLDS["max_tag_drop"] + 1e-9 for d in drops.values()),
                           f"worst: {worst[0]} {-worst[1]:+.0%}"))
    items.append(_item("zero_expected_absent", "Zero expected_absent hits in sent openers",
                       not s["expected_absent_hits"], str(len(s["expected_absent_hits"]))))
    items.append(_item("zero_filter_violations",
                       "Zero filter-detectable violations in sent openers (by construction "
                       "for the new pipeline (same filter); meaningful for legacy)",
                       not s["filter_violations"], str(len(s["filter_violations"]))))
    if base is None:
        items.append(_item("question_rate_below_legacy", "Question-ending rate below legacy",
                           None, "needs a legacy run"))
    else:
        q, bq = s["question_rate"], base["question_rate"]
        ok = q is not None and bq is not None and q < bq
        items.append(_item("question_rate_below_legacy", "Question-ending rate below legacy",
                           ok, f"{_pct(q)} vs legacy {_pct(bq)}"))
    statuses = {i["status"] for i in items}
    return {"items": items, "complete": "n/a" not in statuses,
            "passed": statuses == {"pass"}}


# ============================================================================ output

def _pct(v):
    return "-" if v is None else f"{v:.0%}"


def _fmt_mix(d: dict) -> str:
    if not d:
        return "-"
    return ", ".join(f"{k}: {_pct(v) if isinstance(v, float) else v}"
                     for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def _summary_rows(s: dict) -> list[tuple[str, str]]:
    tow = s["top_opening_word"]
    return [
        ("Backend / requested model", f"{s['backend']} / {s['model']}"),
        ("Rows / likes / skips / errors",
         f"{s['rows']} / {s['likes']} / {s['skips']} / {s['errors']}"),
        ("Coverage (non-empty among likes)", _pct(s["coverage"])),
        ("Coverage (non-empty among all rows)", _pct(s["coverage_all_rows"])),
        ("Coverage non-sparse / sparse",
         f"{_pct(s['coverage_non_sparse'])} / {_pct(s['coverage_sparse'])}"),
        ("Coverage by tag", _fmt_mix(s["coverage_by_tag"])),
        ("Empty reasons", _fmt_mix(s["empty_reasons"])),
        ("Premade rate / rewrite rate", f"{_pct(s['premade_rate'])} / {_pct(s['rewrite_rate'])}"),
        ("Predicted target mix", _fmt_mix(s["target_mix"])),
        ("Non-empty openers (quality base)", str(s["nonempty"])),
        ("Question-ending rate", _pct(s["question_rate"])),
        ("Median length", "-" if s["median_length"] is None else str(s["median_length"])),
        ("Banned-phrase hits", _fmt_mix(s["banned_hits"])),
        ("Filter-detectable violations", str(len(s["filter_violations"]))),
        ("Grounded in source profile", _pct(s["grounded_rate"])),
        ("Top opening word", "-" if not tow else f"{tow[0]} ({_pct(tow[1])})"),
        ("First-3-words repeat rate", _pct(s["opening3_repeat_rate"])),
        ("expected_absent hits", str(len(s["expected_absent_hits"]))),
        ("Shared-experience / sender-bio flags", str(len(s["sender_bio_flags"]))),
        ("Anchor kind mix", _fmt_mix(s["anchor_kind_mix"])),
        ("Form mix", _fmt_mix(s["form_mix"])),
    ]


def _flag_lines(s: dict) -> list[str]:
    out = []
    for h in s["expected_absent_hits"]:
        out.append(f"expected_absent '{h['term']}' in {h['profile_id']}: {h['message']}")
    for v in s["filter_violations"]:
        out.append(f"filter {','.join(v['codes'])} in {v['profile_id']}: {v['message']}")
    for b in s["sender_bio_flags"]:
        out.append(f"sender-bio in {b['profile_id']}: {b['message']}")
    return out


def _side_by_side(runs: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict | None]]]:
    ids = []
    for _, rows in runs:
        for r in rows:
            if r.get("profile_id") not in ids:
                ids.append(r.get("profile_id"))
    index = [{r.get("profile_id"): r for r in rows} for _, rows in runs]
    return [(pid, [ix.get(pid) for ix in index]) for pid in ids]


def _cell(r: dict | None) -> tuple[str, str, str]:
    if r is None:
        return ("(not run)", "-", "-")
    msg = _msg(r) or ("(error: " + str(r.get("error")) + ")" if r.get("status") == "error"
                      else "(empty)")
    anchor = r.get("anchor_kind") or "-"
    if r.get("anchor_label"):
        anchor += f": {r['anchor_label']}"
    return (msg, str(r.get("status")), anchor)


def _md(runs, summaries, generated) -> str:
    names = [n for n, _ in runs]
    L = ["# Opener eval report", "", f"> **{DISCLAIMER}**", "", f"Generated {generated}.",
         ""] + [f"- {n}: backend `{s['backend']}`, requested model `{s['model']}`"
                for n, s in zip(names, summaries)] + [
         "", "## Summary", "", "| Metric | " + " | ".join(names) + " |",
         "|---|" + "---|" * len(names)]
    for i, (label, _) in enumerate(_summary_rows(summaries[0])):
        vals = [_summary_rows(s)[i][1] for s in summaries]
        L.append(f"| {label} | " + " | ".join(v.replace("|", "/") for v in vals) + " |")
    acc = summaries[-1]["acceptance"]
    L += ["", f"## Acceptance ({names[-1]}; heuristic)", "",
          f"Overall: **{'PASS' if acc['passed'] else 'NOT PASSED'}**"
          + ("" if acc["complete"] else " (incomplete: some items need a legacy run)"),
          "", "| Target | Status | Detail |", "|---|---|---|"]
    L += [f"| {i['label']} | {i['status']} | {i['detail']} |" for i in acc["items"]]
    for name, s in zip(names, summaries):
        flags = _flag_lines(s)
        if flags:
            L += ["", f"### Flags: {name}", ""] + [f"- {f}" for f in flags]
    L += ["", "## Per profile", "", "| Profile | " + " | ".join(names) + " |",
          "|---|" + "---|" * len(names)]
    for pid, cells in _side_by_side(runs):
        parts = []
        for r in cells:
            m, st, a = _cell(r)
            parts.append(f"{m} [{st}; {a}]".replace("|", "/"))
        L.append(f"| {pid} | " + " | ".join(parts) + " |")
    return "\n".join(L) + "\n"


_CSS = """body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:24px;
color:#1a1a1a;background:#fafafa;max-width:1200px}
table{border-collapse:collapse;width:100%;margin:12px 0 24px;background:#fff}
th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top;font-size:14px}
th{background:#f0f0f0}.disc{background:#fff6d6;border:1px solid #e6c200;padding:10px;
font-weight:600}.pass{color:#11772d;font-weight:600}.fail{color:#b00020;font-weight:600}
.na{color:#777}.meta{color:#666;font-size:12px}ul{font-size:14px}"""


def _html(runs, summaries, generated) -> str:
    e = html.escape
    names = [n for n, _ in runs]
    H = ["<!doctype html><html><head><meta charset='utf-8'>",
         "<title>Opener eval report</title>", f"<style>{_CSS}</style></head><body>",
         "<h1>Opener eval report</h1>", f"<p class='disc'>{e(DISCLAIMER)}</p>",
         f"<p class='meta'>Generated {e(generated)}.</p>",
         "<ul class='meta'>" + "".join(
             f"<li>{e(n)}: backend {e(s['backend'])}, requested model {e(s['model'])}</li>"
             for n, s in zip(names, summaries)) + "</ul>",
         "<h2>Summary</h2>",
         "<table><tr><th>Metric</th>" + "".join(f"<th>{e(n)}</th>" for n in names) + "</tr>"]
    rows_by = [_summary_rows(s) for s in summaries]
    for i, (label, _) in enumerate(rows_by[0]):
        H.append(f"<tr><td>{e(label)}</td>"
                 + "".join(f"<td>{e(rb[i][1])}</td>" for rb in rows_by) + "</tr>")
    H.append("</table>")
    acc = summaries[-1]["acceptance"]
    H += [f"<h2>Acceptance ({e(names[-1])}; heuristic)</h2>",
          f"<p>Overall: <span class='{'pass' if acc['passed'] else 'fail'}'>"
          f"{'PASS' if acc['passed'] else 'NOT PASSED'}</span>"
          + ("" if acc["complete"] else " (incomplete: some items need a legacy run)") + "</p>",
          "<table><tr><th>Target</th><th>Status</th><th>Detail</th></tr>"]
    for i in acc["items"]:
        cls = {"pass": "pass", "fail": "fail"}.get(i["status"], "na")
        H.append(f"<tr><td>{e(i['label'])}</td><td class='{cls}'>{e(i['status'])}</td>"
                 f"<td>{e(i['detail'])}</td></tr>")
    H.append("</table>")
    for name, s in zip(names, summaries):
        flags = _flag_lines(s)
        if flags:
            H.append(f"<h3>Flags: {e(name)}</h3><ul>"
                     + "".join(f"<li>{e(f)}</li>" for f in flags) + "</ul>")
    H += ["<h2>Per profile</h2>", "<table><tr><th>Profile</th>"
          + "".join(f"<th>{e(n)}</th><th>status</th><th>anchor</th>" for n in names) + "</tr>"]
    for pid, cells in _side_by_side(runs):
        tds = "".join("".join(f"<td>{e(x)}</td>" for x in _cell(r)) for r in cells)
        H.append(f"<tr><td>{e(str(pid))}</td>{tds}</tr>")
    H.append("</table></body></html>")
    return "\n".join(H) + "\n"


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _label(path: Path, rows: list[dict]) -> str:
    pipes = {r.get("pipeline") for r in rows}
    return f"{'/'.join(sorted(map(str, pipes)))} ({Path(path).name})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="one or two run JSONL files")
    ap.add_argument("--out", type=Path, help="output path stem (writes .md and .html)")
    args = ap.parse_args(argv)
    if len(args.files) > 2:
        ap.error("pass one or two JSONL files")

    runs = [(f, _read_jsonl(f)) for f in args.files]
    # Baseline (legacy) first so the candidate run comes last.
    runs.sort(key=lambda fr: 0 if all(r.get("pipeline") == "legacy" for r in fr[1])
              and fr[1] else 1)
    profiles = load_profiles()
    summaries, base = [], None
    if len(runs) == 2 and runs[0][1] and all(r.get("pipeline") == "legacy" for r in runs[0][1]):
        base = summarise(runs[0][1], profiles)
        summaries = [base, summarise(runs[1][1], profiles, baseline=base)]
    else:
        summaries = [summarise(rows, profiles) for _, rows in runs]

    named = [(_label(f, rows), rows) for f, rows in runs]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stem = args.out or (render.HERE / "results" /
                        f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    stem = stem.with_suffix("") if stem.suffix in (".md", ".html") else stem
    stem.parent.mkdir(parents=True, exist_ok=True)
    md_path, html_path = Path(f"{stem}.md"), Path(f"{stem}.html")
    md_path.write_text(_md(named, summaries, generated), encoding="utf-8")
    html_path.write_text(_html(named, summaries, generated), encoding="utf-8")
    acc = summaries[-1]["acceptance"]
    print(f"coverage {_pct(summaries[-1]['coverage'])}; acceptance "
          f"{'PASS' if acc['passed'] else 'NOT PASSED'}")
    print(f"wrote {md_path}\nwrote {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
