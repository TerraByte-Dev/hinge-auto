"""Ollama backend for HingeAuto judging.

Targets a vision-capable model running on either:
  - Local Ollama  (`ollama serve` on your machine, default
    `http://localhost:11434`)
  - Ollama Cloud  (https://ollama.com — free tier available; set
    `OLLAMA_HOST=https://ollama.com` and `OLLAMA_API_KEY=...`)

The same JSON tool schema as the Anthropic backend is used (via
`judge_common.DECIDE_INPUT_SCHEMA`), but Ollama's tool format is
OpenAI-compatible so it gets wrapped differently.

Honest tradeoff vs Anthropic: open vision models (`qwen2.5-vl`,
`llama3.2-vision`) are noticeably less reliable at extracting structured
output across a 7-frame profile, and the opener writing is weaker. Cost
is the win — free if you self-host or stay inside Ollama Cloud's free
tier.

Set in config.py:
  JUDGE_BACKEND = "ollama"
  OLLAMA_MODEL  = "qwen2.5-vl"        # or "llama3.2-vision"
  OLLAMA_HOST   = None                # default localhost; cloud:
                                      # "https://ollama.com"

Set in your .env (or shell):
  OLLAMA_API_KEY=...   # required for Ollama Cloud, ignored locally
"""

import base64
import json
import os

import config
from judge_common import (
    DECIDE_INPUT_SCHEMA,
    Decision,
    build_system_prompt,
    decision_from_dict,
    enforce_premade_verbatim,
)


def _client():
    try:
        from ollama import Client
    except ImportError as e:
        raise RuntimeError(
            "The `ollama` package isn't installed. Run "
            "`pip install ollama` (or install the optional extras: "
            "`pip install -r requirements-ollama.txt`)."
        ) from e

    host = getattr(config, "OLLAMA_HOST", None) or os.environ.get(
        "OLLAMA_HOST", "http://localhost:11434"
    )
    kwargs = {"host": host}
    api_key = os.environ.get("OLLAMA_API_KEY")
    if api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
    return Client(**kwargs)


def _tool_spec(schema: dict = DECIDE_INPUT_SCHEMA,
               name: str = "submit_decision") -> dict:
    """OpenAI-compatible tool envelope wrapping the shared schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Submit a like/skip decision for this Hinge profile.",
            "parameters": schema,
        },
    }


def _images_b64(frames: list[bytes]) -> list[str]:
    return [base64.standard_b64encode(f).decode("utf-8") for f in frames]


def _decision_from_args(args: dict, usage: dict) -> Decision:
    """Build Decision from a tool-call argument dict (tolerant)."""
    return decision_from_dict(args, usage)


def vision_json(system: str, frames_png: list[bytes], schema: dict,
                name: str) -> dict:
    """One vision call offering tool `name` (parameters `schema`) over the
    ordered PNG frames of one profile. Returns the tool arguments (or, as a
    fallback, a JSON object found in the message content) plus `_usage`.
    Raises RuntimeError when neither is usable. Used by `judge` and by the
    offline eval (on the `opener-evals` branch)."""
    client = _client()
    model = getattr(config, "OLLAMA_MODEL", "qwen2.5-vl")

    user_text = (
        f"Above are {len(frames_png)} screenshots of one Hinge profile, in order "
        "from top to bottom. Decide whether to like or skip, and call the "
        f"{name} tool with the structured result."
    )

    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": user_text,
                "images": _images_b64(frames_png),
            },
        ],
        tools=[_tool_spec(schema, name)],
        # Hint Ollama toward JSON if it falls back to content output
        # instead of a tool call.
        options={"temperature": 0.2},
    )

    usage = {
        "input_tokens": getattr(response, "prompt_eval_count", 0) or 0,
        "output_tokens": getattr(response, "eval_count", 0) or 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    if isinstance(response, dict):
        usage["input_tokens"] = response.get("prompt_eval_count", 0) or 0
        usage["output_tokens"] = response.get("eval_count", 0) or 0

    message = response.get("message") if isinstance(response, dict) else response.message
    tool_calls = (
        message.get("tool_calls") if isinstance(message, dict)
        else getattr(message, "tool_calls", None)
    ) or []

    for call in tool_calls:
        fn = call["function"] if isinstance(call, dict) else call.function
        fn_name = fn["name"] if isinstance(fn, dict) else fn.name
        args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
        if fn_name != name:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        out = dict(args) if isinstance(args, dict) else {}
        out["_usage"] = usage
        return out

    # Fallback: some models return JSON in `content` instead of a tool call
    content = (
        message.get("content") if isinstance(message, dict)
        else getattr(message, "content", "")
    ) or ""
    if content:
        # Strip code fences / leading prose
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                data["_usage"] = usage
                return data

    raise RuntimeError(
        f"Ollama ({model}) did not return a usable {name} call. "
        f"Try a different OLLAMA_MODEL (e.g. 'qwen2.5-vl:7b' or "
        f"'llama3.2-vision:11b') or check that the model is pulled."
    )


def judge(frames: list[bytes]) -> Decision:
    """Given an ordered list of PNG frames of one profile, return a Decision."""
    out = vision_json(build_system_prompt(), frames, DECIDE_INPUT_SCHEMA,
                      "submit_decision")
    usage = out.pop("_usage")
    decision = _decision_from_args(out, usage)
    enforce_premade_verbatim(decision)
    return decision


def complete_json(system: str, user: str, schema: dict, name: str) -> dict:
    """Text-only structured call (opener stage 2) using Ollama's
    `format=<json schema>`; returns the parsed object plus `_usage`.
    `name` is unused (no tool envelope). Raises on failure; the caller
    (opener.finalize) catches."""
    client = _client()
    response = client.chat(
        model=getattr(config, "OLLAMA_MODEL", "qwen2.5-vl"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        format=schema,
        options={"temperature": 0.2},
    )
    message = response.get("message") if isinstance(response, dict) else response.message
    content = (
        message.get("content") if isinstance(message, dict)
        else getattr(message, "content", "")
    ) or ""
    out = json.loads(content)
    if not isinstance(out, dict):
        raise RuntimeError(f"Ollama {name}: expected a JSON object, got {type(out).__name__}")

    def _get(key):
        return (response.get(key) if isinstance(response, dict)
                else getattr(response, key, 0)) or 0

    out["_usage"] = {"input_tokens": _get("prompt_eval_count"),
                     "output_tokens": _get("eval_count")}
    return out
