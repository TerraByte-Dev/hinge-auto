import json
from pathlib import Path

from PIL import Image

from evals.openers import legacy_prompt, render

PROFILES = json.loads(
    (Path(render.__file__).parent / "profiles.json").read_text(encoding="utf-8")
)
TAGS = {"sparse", "ambiguous_image", "serious", "playful", "mixed"}


def test_ids_unique_and_tagged():
    ids = [p["id"] for p in PROFILES]
    assert len(ids) == len(set(ids))
    for p in PROFILES:
        assert set(p["tags"]) & TAGS
        assert set(p["tags"]) <= TAGS
        assert p["elements"][0]["kind"] == "photo"
        assert isinstance(p["expected_absent"], list)


def test_count_and_tag_coverage():
    assert len(PROFILES) >= 24
    for t in TAGS:
        assert sum(t in p["tags"] for p in PROFILES) >= 4, t


def test_render_profile(tmp_path):
    paths = render.render_profile(PROFILES[0], tmp_path)
    assert paths
    for p in paths:
        assert Image.open(p).width == 540
    top = Image.open(paths[0]).convert("RGB").crop((0, 0, 540, 120))
    assert len(top.getcolors(1 << 20)) > 1


def test_render_all_profiles_no_blank_tail(tmp_path):
    for p in PROFILES:
        paths = render.render_profile(p, tmp_path / p["id"])
        last = Image.open(paths[-1]).convert("RGB")
        assert len(last.getcolors(1 << 24)) > 1, p["id"]


def test_legacy_prompt_is_old():
    assert "followed by a short question" in legacy_prompt.SYSTEM_PROMPT
    assert "prompt_curious" in legacy_prompt.SYSTEM_PROMPT
    assert "message_archetype" in legacy_prompt.SCHEMA["properties"]


def test_expected_absent_not_in_own_profile():
    import re

    for p in PROFILES:
        text = " ".join(
            [p["info"], p["name"]]
            + [
                " ".join(str(v) for k, v in e.items() if k != "kind")
                for e in p["elements"]
            ]
        )
        for term in p["expected_absent"]:
            assert not re.search(rf"\b{re.escape(term)}\b", text, re.I), (
                p["id"], term)
