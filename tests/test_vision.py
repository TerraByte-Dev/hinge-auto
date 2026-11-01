"""vision.find_first_heart on synthetic frames (no real profile photos)."""

import io

import numpy as np
from PIL import Image, ImageDraw

import vision


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _new_ui_frame(center: tuple[int, int]) -> Image.Image:
    """Phone-style frame: dark (26,26,26) heart circle ~144px over a photo
    whose dark clothing touches the circle, white heart glyph inside."""
    img = Image.new("RGB", (1080, 2340), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((60, 520, 1020, 1480), fill=(120, 110, 95))   # photo
    d.rectangle((300, 1250, 1020, 1480), fill=(36, 36, 34))   # dark jeans
    cx, cy = center
    d.ellipse((cx - 72, cy - 72, cx + 72, cy + 72), fill=(26, 26, 26))
    d.ellipse((cx - 30, cy - 25, cx + 30, cy + 25), fill=(255, 254, 253))
    return img


def test_finds_dark_heart_circle_touching_dark_photo():
    got = vision.find_first_heart(_png(_new_ui_frame((917, 1387))))

    assert got is not None
    assert abs(got[0] - 917) <= 5 and abs(got[1] - 1387) <= 5


def test_finds_dark_heart_when_same_grey_streaks_touch_it():
    # Real S23 frame: dark jeans contain thin runs of the exact (26,26,26)
    # that connect to the circle and widen its blob to ~240px.
    img = _new_ui_frame((917, 1387))
    d = ImageDraw.Draw(img)
    for y in range(1330, 1450, 12):
        d.line((745, y, 850, y), fill=(26, 26, 26), width=3)

    got = vision.find_first_heart(_png(img))

    assert got is not None
    assert abs(got[0] - 917) <= 5 and abs(got[1] - 1387) <= 5


def test_picks_topmost_dark_heart():
    img = _new_ui_frame((917, 1387))
    d = ImageDraw.Draw(img)
    d.ellipse((845, 2000 - 72, 989, 2000 + 72), fill=(26, 26, 26))

    got = vision.find_first_heart(_png(img))

    assert got is not None and abs(got[1] - 1387) <= 5


def test_still_finds_old_white_heart():
    img = Image.new("RGB", (1080, 2424), (120, 110, 95))
    d = ImageDraw.Draw(img)
    d.ellipse((875, 1358, 1001, 1484), fill=(255, 255, 255))
    d.ellipse((918, 1404, 958, 1438), fill=(40, 40, 40))  # heart glyph cutout

    got = vision.find_first_heart(_png(img))

    assert got is not None
    assert abs(got[0] - 938) <= 5 and abs(got[1] - 1421) <= 5


def test_ignores_dark_blob_on_left():
    img = Image.new("RGB", (1080, 2340), (245, 245, 245))
    ImageDraw.Draw(img).ellipse((100, 1300, 244, 1444), fill=(26, 26, 26))

    assert vision.find_first_heart(_png(img)) is None


# ---------- Send Like (compose card) ----------

def _compose_frame(keyboard: bool = True, send: bool = True) -> Image.Image:
    """Phone compose card as measured on the S23: lavender Rose pill on the
    left, flat (26,26,26) 'Send Priority Like' pill on the right, and the
    keyboard's full-width toolbar strip in the very same grey below."""
    img = Image.new("RGB", (1080, 2340), (255, 254, 253))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((48, 868, 1032, 1068), 30, outline=(225, 225, 225), width=3)
    d.rounded_rectangle((108, 1107, 408, 1230), 60, fill=(188, 167, 192))  # Rose
    if send:
        d.rounded_rectangle((444, 1107, 972, 1230), 60, fill=(26, 26, 26))
        d.text((600, 1160), "Send Priority Like", fill=(255, 255, 255))
    if keyboard:
        d.rectangle((0, 1314, 1080, 1446), fill=(26, 26, 26))   # toolbar
        d.rectangle((0, 1446, 1080, 2340), fill=(23, 23, 26))   # keys bg
        d.rounded_rectangle((290, 2050, 790, 2160), 20, fill=(52, 52, 54))
    return img


def test_finds_black_send_like_pill():
    got = vision.find_send_like(_png(_compose_frame()))

    assert got is not None
    assert abs(got[0] - 708) <= 5 and abs(got[1] - 1168) <= 5


def test_send_like_ignores_keyboard_strip_when_pill_absent():
    assert vision.find_send_like(_png(_compose_frame(send=False))) is None


def test_send_like_rejects_dark_pill_on_left_half():
    # Whatever sits left of centre (where the Rose button lives) is never
    # Send Like, even if it happens to be the right grey.
    img = _compose_frame(keyboard=False, send=False)
    ImageDraw.Draw(img).rounded_rectangle((60, 1107, 480, 1230), 60, fill=(26, 26, 26))

    assert vision.find_send_like(_png(img)) is None


def test_still_finds_old_peach_send_like():
    img = Image.new("RGB", (1080, 2424), (255, 255, 255))
    ImageDraw.Draw(img).rounded_rectangle((390, 1434, 985, 1543), 50, fill=(240, 210, 195))

    got = vision.find_send_like(_png(img))

    assert got is not None and abs(got[0] - 687) <= 5 and abs(got[1] - 1488) <= 5


# ---------- explain mode ----------

def test_explain_heart_reports_accepted_blob():
    xy, report = vision.find_first_heart(_png(_new_ui_frame((917, 1387))), explain=True)

    assert xy is not None
    accepted = [r for r in report if r["verdict"] == "accepted"]
    assert len(accepted) == 1 and accepted[0]["mask"] == "dark_grey"


def test_explain_send_like_reports_keyboard_strip_rejected_by_size():
    xy, report = vision.find_send_like(_png(_compose_frame(send=False)), explain=True)

    assert xy is None
    strip = [r for r in report if r["mask"] == "black_pill"
             and r["bbox"][2] - r["bbox"][0] == 1080]
    assert strip and strip[0]["verdict"] == "size"


def test_explain_send_like_reports_left_pill_rejected_by_x():
    img = _compose_frame(keyboard=False, send=False)
    ImageDraw.Draw(img).rounded_rectangle((60, 1107, 480, 1230), 60, fill=(26, 26, 26))

    _, report = vision.find_send_like(_png(img), explain=True)

    assert any(r["verdict"] == "x<=500" for r in report)


def test_explain_default_return_unchanged():
    got = vision.find_first_heart(_png(_new_ui_frame((917, 1387))))

    assert isinstance(got, tuple) and len(got) == 2 and all(isinstance(v, int) for v in got)
