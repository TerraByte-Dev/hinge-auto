"""identity: capture / near-match calibration / verify (spec §6)."""

import io
import random
from functools import lru_cache

import numpy as np
import pytest
from PIL import Image, ImageDraw

import config
import identity
import ui

W, H = 1080, 2340
VIEW_STEP = 700          # pixels one scroll position moves the content


@lru_cache(maxsize=None)
def _content(seed: int, base=None) -> Image.Image:
    """A tall rendered 'profile': photo 1, name, a prompt card, more photos.
    `base` forces the photo's base colour (to build hard look-alikes)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, 6000), (250, 248, 252))
    d = ImageDraw.Draw(img)
    for top in (60, 2300, 3900):          # three photos
        c0 = base or tuple(rnd.randrange(40, 220) for _ in range(3))
        for y in range(top, top + 1300, 10):  # vertical gradient
            k = (y - top) / 1300
            d.rectangle((40, y, W - 40, y + 10),
                        fill=tuple(int(v * (1 - 0.5 * k)) for v in c0))
        for _ in range(8):                    # "people / objects"
            x, y = rnd.randrange(40, W - 300), rnd.randrange(top, top + 1000)
            d.ellipse((x, y, x + rnd.randrange(120, 420), y + rnd.randrange(120, 420)),
                      fill=tuple(rnd.randrange(0, 256) for _ in range(3)))
    d.text((60, 1400), f"Name{seed}", fill=(20, 20, 20))
    d.rectangle((40, 1500, W - 40, 2200), fill=(255, 255, 255))
    d.text((80, 1540), f"My simple pleasures {seed}", fill=(20, 20, 20))
    return img


def render(seed: int, position: int = 0, clock: str = "9:41", noise: int = 0,
           base=None) -> bytes:
    """PNG screen of profile `seed` scrolled to `position` (0 = top)."""
    content = _content(seed, base)
    off = min(position * VIEW_STEP, content.height - H)
    screen = Image.new("RGB", (W, H), (255, 255, 255))
    screen.paste(content.crop((0, off, W, off + H - 340)), (0, 120))
    d = ImageDraw.Draw(screen)
    d.rectangle((0, 0, W, 120), fill=(255, 255, 255))
    d.text((40, 40), clock, fill=(0, 0, 0))
    d.rectangle((0, H - 220, W, H), fill=(245, 245, 245))
    d.text((100, H - 150), "Discover  Standouts  Likes You", fill=(0, 0, 0))
    if noise:
        arr = np.asarray(screen).astype(int)
        arr += np.random.default_rng(seed).integers(-noise, noise + 1, arr.shape)
        screen = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    buf = io.BytesIO()
    screen.save(buf, format="PNG")
    return buf.getvalue()


def mean_diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def nodes_for(name: str | None):
    base = [ui.UiNode("Discover", 0, 2200, 200, 2340)]
    return base + ([ui.UiNode(f"Skip {name}", 60, 1770, 246, 1956)] if name else [])


@pytest.fixture
def phone_device(monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "galaxy_s23")


# ---------- calibration of IDENTITY["thumb_max_diff"] ----------

def test_config_values():
    assert config.IDENTITY == {"thumb_size": (64, 128), "thumb_max_diff": 6.0}


def test_identical_frames_near_match():
    a = render(1)
    assert identity.near_match(identity.thumb(a), identity.thumb(render(1)))


def test_clock_change_near_matches():
    a, b = identity.thumb(render(1, clock="9:41")), identity.thumb(render(1, clock="12:58"))
    assert identity.near_match(a, b) and mean_diff(a, b) == 0


def test_rerendered_frame_with_pixel_noise_near_matches():
    a, b = identity.thumb(render(2)), identity.thumb(render(2, noise=6))
    assert identity.near_match(a, b)


@pytest.mark.parametrize("s1,s2", [(1, 2), (3, 4), (5, 6), (7, 8)])
def test_different_profiles_do_not_match(s1, s2):
    a, b = identity.thumb(render(s1)), identity.thumb(render(s2))
    assert not identity.near_match(a, b)
    assert mean_diff(a, b) > 2 * config.IDENTITY["thumb_max_diff"]


def test_lookalike_profiles_same_base_colour_do_not_match():
    a = identity.thumb(render(11, base=(120, 110, 100)))
    b = identity.thumb(render(12, base=(120, 110, 100)))
    assert not identity.near_match(a, b)


def test_same_profile_scrolled_does_not_match_top():
    assert not identity.near_match(identity.thumb(render(1, 0)), identity.thumb(render(1, 1)))


def test_near_match_rejects_empty_or_mismatched_sizes():
    assert not identity.near_match(b"", b"")
    assert not identity.near_match(b"\x00" * 10, b"\x00" * 11)


def test_thumb_is_configured_size():
    assert len(identity.thumb(render(1))) == 64 * 128


# ---------- labels / capture ----------

def test_skip_label_first_skip_node():
    nodes = [ui.UiNode("Skip", 0, 0, 1, 1), *nodes_for("Sam"), ui.UiNode("Skip Zed", 0, 0, 1, 1)]
    assert identity.skip_label(nodes) == "Skip Sam"
    assert identity.skip_label(None) is None
    assert identity.skip_label([]) is None


def test_capture_records_label_thumb_and_hash():
    png = render(1)
    ident = identity.capture(png, nodes_for("Sam"))
    assert ident.skip_label == "Skip Sam"
    assert ident.top_thumb == identity.thumb(png)
    assert ident.top_hash == identity.region_hash(png)
    assert identity.capture(png, None, top_hash="h").top_hash == "h"
    assert identity.capture(png, None).skip_label is None


def test_region_hash_ignores_clock():
    assert identity.region_hash(render(1, clock="1:00")) == identity.region_hash(render(1, clock="2:00"))


# ---------- verify ----------

def test_verify_ok(phone_device):
    ident = identity.capture(render(1), nodes_for("Sam"))
    assert identity.verify(ident, render(1, clock="9:42"), nodes_for("Sam")) is None


def test_verify_name_mismatch(phone_device):
    ident = identity.capture(render(1), nodes_for("Sam"))
    assert identity.verify(ident, render(1), nodes_for("Alex")) == "name_mismatch"


def test_verify_same_name_different_profile_thumb_mismatch(phone_device):
    ident = identity.capture(render(1), nodes_for("Sam"))
    assert identity.verify(ident, render(2), nodes_for("Sam")) == "thumb_mismatch"


def test_verify_no_label_at_capture(phone_device):
    ident = identity.capture(render(1), None)
    assert identity.verify(ident, render(1), nodes_for("Sam")) == "no_label"


def test_verify_no_label_now(phone_device):
    ident = identity.capture(render(1), nodes_for("Sam"))
    assert identity.verify(ident, render(1), None) == "no_label"
    assert identity.verify(ident, render(1), nodes_for(None)) == "no_label"


def test_verify_unreadable_png(phone_device):
    ident = identity.capture(render(1), nodes_for("Sam"))
    assert identity.verify(ident, b"not a png", nodes_for("Sam")) == "thumb_unreadable"


def test_emulator_none_label_passes_on_thumbnail(monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    ident = identity.capture(render(1), None)
    assert ident.skip_label is None
    assert identity.verify(ident, render(1), None) is None
    assert identity.verify(ident, render(1), nodes_for("Sam")) is None


def test_emulator_still_checks_thumbnail_and_readable_names(monkeypatch):
    monkeypatch.setattr(config, "DEVICE", "emulator")
    assert identity.verify(identity.capture(render(1), None), render(2), None) == "thumb_mismatch"
    ident = identity.capture(render(1), nodes_for("Sam"))
    assert identity.verify(ident, render(1), nodes_for("Alex")) == "name_mismatch"
