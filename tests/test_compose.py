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
