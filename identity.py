"""Which profile is on screen: the identity recorded with the judged frames.

Spec §6: a `ProfileIdentity` is captured at the top of the profile at the
moment frames[0] is taken, passed unchanged into do_like, and verified
against the live screen before any heart is tapped. Must not import main.
"""

import hashlib
import io
from dataclasses import dataclass

from PIL import Image

import config


@dataclass(frozen=True)
class ProfileIdentity:
    skip_label: str | None   # "Skip <name>" from one UI dump; None if unreadable
    top_thumb: bytes         # grayscale downscaled region crop of frames[0]
    top_hash: str            # main._profile_region_hash(frames[0])


@dataclass
class ConfirmBaseline:
    pre_tap_hash: str               # region hash of the screen at the heart tap
    pre_tap_viewport: str           # "top" | "scrolled"
    identity: ProfileIdentity
    matched_prompt_label: str | None = None


def region_crop(img: Image.Image) -> Image.Image:
    """The frame minus status bar (clock ticks) and bottom nav (badges)."""
    w, h = img.size
    return img.crop((0, 120, w, h - 220))


def region_hash(png: bytes) -> str:
    return hashlib.md5(region_crop(Image.open(io.BytesIO(png))).tobytes()).hexdigest()


def thumb(png: bytes) -> bytes:
    size = tuple(config.IDENTITY["thumb_size"])
    img = region_crop(Image.open(io.BytesIO(png))).convert("L")
    return img.resize(size, Image.BILINEAR).tobytes()


def near_match(a: bytes, b: bytes) -> bool:
    """Mean absolute grayscale difference <= IDENTITY["thumb_max_diff"].
    Empty or differently sized thumbnails never match."""
    if not a or not b or len(a) != len(b):
        return False
    diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return diff <= config.IDENTITY["thumb_max_diff"]


def skip_label(nodes) -> str | None:
    for n in nodes or ():
        if isinstance(n.label, str) and n.label.startswith("Skip "):
            return n.label
    return None


def capture(frame0: bytes, nodes, top_hash: str | None = None) -> ProfileIdentity:
    """Identity from frames[0] and the UI dump taken with it (None if the
    dump failed). `top_hash` lets main pass its own _profile_region_hash."""
    return ProfileIdentity(
        skip_label=skip_label(nodes),
        top_thumb=thumb(frame0),
        top_hash=top_hash if top_hash is not None else region_hash(frame0),
    )


def verify(ident: ProfileIdentity, png: bytes, nodes) -> str | None:
    """None if the live screen (png + its UI dump) is the judged profile at
    the top; otherwise a reason: no_label, name_mismatch, thumb_unreadable,
    thumb_mismatch.

    On the phone both the name label and the thumbnail must match. On the
    emulator (labels may be unavailable) a missing label is tolerated and
    the thumbnail decides; two readable labels that differ still fail."""
    live = skip_label(nodes)
    if config.DEVICE != "emulator":
        if ident.skip_label is None or live is None:
            return "no_label"
        if live != ident.skip_label:
            return "name_mismatch"
    elif ident.skip_label is not None and live is not None and live != ident.skip_label:
        return "name_mismatch"
    try:
        live_thumb = thumb(png)
    except Exception:
        return "thumb_unreadable"
    if not near_match(ident.top_thumb, live_thumb):
        return "thumb_mismatch"
    return None
