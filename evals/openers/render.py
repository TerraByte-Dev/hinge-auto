"""Render fictional eval profiles into Hinge-like PNG frames.

    python -m evals.openers.render --all [--out DIR]
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
PROFILES_PATH = HERE / "profiles.json"
DEFAULT_OUT = HERE / "results" / "frames"

BG = (245, 243, 240)
INK = (26, 26, 26)
MUTED = (110, 110, 110)
PHOTO_BG = (170, 170, 175)
CARD = (255, 255, 255)
MARGIN = 20
GAP = 16


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _line_h(font):
    return int(font.getbbox("Ag")[3] * 1.35) + 2


def load_profiles(path: Path = PROFILES_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_profile(p: dict, out_dir: Path, width: int = 540,
                   frame_h: int = 1170) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    big, body, small = _font(30), _font(24), _font(20)
    scratch = ImageDraw.Draw(Image.new("RGB", (width, 10)))
    inner = width - 2 * MARGIN
    ops = []  # (height, draw_fn)

    # Header: name, age, info row.
    head_lines = _wrap(scratch, p.get("info", ""), small, inner)
    head_h = 24 + _line_h(big) + len(head_lines) * _line_h(small) + 24

    def draw_header(d, y, p=p, head_lines=head_lines):
        d.rectangle([0, y, width, y + head_h], fill=(255, 255, 255))
        d.text((MARGIN, y + 20), f"{p['name'].title()}, {p['age']}",
               fill=INK, font=big)
        ty = y + 20 + _line_h(big)
        for ln in head_lines:
            d.text((MARGIN, ty), ln, fill=MUTED, font=small)
            ty += _line_h(small)
        d.line([0, y + head_h - 1, width, y + head_h - 1], fill=(220, 220, 220))
    ops.append((head_h, draw_header))

    photo_n = 0
    for el in p["elements"]:
        if el["kind"] == "photo":
            photo_n += 1
            cap = f"[photo {photo_n}] {el.get('caption', '')}"
            lines = _wrap(scratch, cap, body, inner - 32)
            ph = max(420, 40 + len(lines) * _line_h(body))

            def draw_photo(d, y, lines=lines, ph=ph):
                d.rounded_rectangle([MARGIN, y, width - MARGIN, y + ph],
                                    radius=14, fill=PHOTO_BG)
                ty = y + 20
                for ln in lines:
                    d.text((MARGIN + 16, ty), ln, fill=INK, font=body)
                    ty += _line_h(body)
            ops.append((ph, draw_photo))
        else:
            q = _wrap(scratch, el["prompt"], small, inner - 40)
            a = _wrap(scratch, el["answer"], big, inner - 40)
            ch = 24 + len(q) * _line_h(small) + 8 + len(a) * _line_h(big) + 24

            def draw_prompt(d, y, q=q, a=a, ch=ch):
                d.rounded_rectangle([MARGIN, y, width - MARGIN, y + ch],
                                    radius=14, fill=CARD,
                                    outline=(225, 225, 225))
                ty = y + 24
                for ln in q:
                    d.text((MARGIN + 20, ty), ln, fill=MUTED, font=small)
                    ty += _line_h(small)
                ty += 8
                for ln in a:
                    d.text((MARGIN + 20, ty), ln, fill=INK, font=big)
                    ty += _line_h(big)
            ops.append((ch, draw_prompt))

    total = sum(h for h, _ in ops) + GAP * (len(ops) - 1) + 2 * GAP
    canvas = Image.new("RGB", (width, total), BG)
    d = ImageDraw.Draw(canvas)
    y = 0
    for i, (h, fn) in enumerate(ops):
        y += GAP if i else 0
        fn(d, y)
        y += h
    canvas = canvas.crop((0, 0, width, y + GAP))

    tops = list(range(0, canvas.height, frame_h))
    if len(tops) > 1 and canvas.height - tops[-1] < 200:
        tops[-1] = canvas.height - frame_h  # avoid a near-empty tail frame
    paths = []
    for i, top in enumerate(tops):
        piece = canvas.crop((0, top, width, min(top + frame_h, canvas.height)))
        path = out_dir / f"frame_{i:02d}.png"
        piece.save(path)
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="render every profile")
    ap.add_argument("--id", help="render one profile id")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    profiles = load_profiles()
    if args.id:
        profiles = [p for p in profiles if p["id"] == args.id]
    elif not args.all:
        ap.error("pass --all or --id")
    for p in profiles:
        paths = render_profile(p, args.out / p["id"])
        print(f"{p['id']}: {len(paths)} frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
