"""Compose-card offsets come from config.COMPOSE (per device)."""

import numpy as np
from PIL import Image, ImageDraw

import config
import vision
from tests.test_vision import _png


def test_comment_input_uses_configured_offset(monkeypatch):
    monkeypatch.setitem(config.COMPOSE, "comment_dy", 200)

    assert vision.find_comment_input((708, 1168)) == (540, 968)


def test_text_pixels_counts_typed_text_inside_comment_box(monkeypatch):
    monkeypatch.setitem(config.COMPOSE, "text_window", (290, 110))
    img = Image.new("RGB", (1080, 2340), (255, 255, 255))
    empty = vision.comment_field_text_pixels(_png(img), (708, 1168))
    # Typed text starts at the box's left edge (x~110), well left of
    # send_x - 350 — the old window missed it.
    ImageDraw.Draw(img).rectangle((110, 900, 340, 940), fill=(0, 0, 0))

    filled = vision.comment_field_text_pixels(_png(img), (708, 1168))

    assert empty == 0 and filled >= 230 * 40


def _box_frame():
    img = Image.new("RGB", (1080, 2340), (255, 254, 253))
    ImageDraw.Draw(img).rounded_rectangle((108, 867, 972, 1070), 30, outline=(225, 225, 225), width=3)
    return img


def test_grey_placeholder_and_cursor_read_as_empty():
    # Empty S23 box: grey "Add a comment" placeholder + black blinking cursor.
    img = _box_frame()
    d = ImageDraw.Draw(img)
    d.rectangle((150, 900, 520, 945), fill=(133, 133, 132))   # placeholder glyph mass
    d.rectangle((148, 895, 152, 955), fill=(26, 26, 26))      # cursor

    px = vision.comment_field_text_pixels(_png(img), (708, 1167))

    assert px < config.COMPOSE["text_min_pixels"]


def test_black_typed_line_reads_as_text():
    # Run 20261101-151639 profile 4: last visible line "toward?" in black,
    # line above faded grey; 1614 near-black px measured in the real frame.
    img = _box_frame()
    d = ImageDraw.Draw(img)
    d.rectangle((150, 925, 900, 960), fill=(133, 133, 132))   # faded line
    d.rectangle((150, 985, 330, 1020), fill=(26, 26, 26))     # "toward?"

    px = vision.comment_field_text_pixels(_png(img), (708, 1167))

    assert px >= config.COMPOSE["text_min_pixels"]
