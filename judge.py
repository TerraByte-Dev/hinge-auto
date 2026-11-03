"""Anthropic backend for HingeAuto judging.

Sends profile screenshots to Claude with a forced tool call to extract a
structured Decision. Shared pieces (system prompt, schema, dataclass)
live in `judge_common.py` so the Ollama backend stays in sync.
"""

import base64

import anthropic

import config
from judge_common import (
    DECIDE_INPUT_SCHEMA,
    Decision,
    build_system_prompt,
    decision_from_dict,
    enforce_premade_verbatim,
)


DECIDE_TOOL = {
    "name": "submit_decision",
    "description": "Submit a like/skip decision for this Hinge profile.",
    "input_schema": DECIDE_INPUT_SCHEMA,
}


def _image_block(png_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png_bytes).decode("utf-8"),
        },
    }


def vision_json(system: str, frames_png: list[bytes], schema: dict,
                name: str) -> dict:
    """One vision call with a forced tool `name` (input schema `schema`) over
    the ordered PNG frames of one profile. Returns the tool input dict plus
    `_usage` token counts; raises RuntimeError when no tool call comes back.
    Used by `judge` and by the offline eval (on the `opener-evals` branch)."""
    client = anthropic.Anthropic()

    content = [_image_block(f) for f in frames_png]
    content.append({
        "type": "text",
        "text": (
            f"Above are {len(frames_png)} screenshots of one Hinge profile, in order "
            "from top to bottom. Decide whether to like or skip."
        ),
    })

    tool = {"name": name, "description": DECIDE_TOOL["description"],
            "input_schema": schema}
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=2000,
        output_config={"effort": config.EFFORT},
        # Cache the system prompt — stable within a session.
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        tools=[tool],
        tool_choice={"type": "tool", "name": name},
        messages=[{"role": "user", "content": content}],
    )

    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "cache_creation_input_tokens":
            getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens":
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }

    for block in response.content:
        if block.type == "tool_use" and block.name == name:
            out = dict(block.input)
            out["_usage"] = usage
            return out

    raise RuntimeError(
        f"Claude did not return a tool_use block. stop_reason={response.stop_reason}"
    )


def judge(frames: list[bytes]) -> Decision:
    """Given an ordered list of PNG frames of one profile, return a Decision."""
    out = vision_json(build_system_prompt(), frames, DECIDE_INPUT_SCHEMA,
                      DECIDE_TOOL["name"])
    usage = out.pop("_usage")
    decision = decision_from_dict(out, usage)
    enforce_premade_verbatim(decision)
    return decision


def complete_json(system: str, user: str, schema: dict, name: str) -> dict:
    """Text-only structured call (opener stage 2): a forced tool call named
    `name` whose input is returned, plus `_usage` token counts. Raises on
    failure; the caller (opener.finalize) catches."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=800,
        system=system,
        tools=[{
            "name": name,
            "description": "Submit the structured result.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": name},
        messages=[{"role": "user", "content": user}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == name:
            out = dict(block.input)
            out["_usage"] = {
                "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
            }
            return out
    raise RuntimeError(
        f"Claude did not return a {name} tool call. "
        f"stop_reason={response.stop_reason}"
    )
