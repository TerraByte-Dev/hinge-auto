"""Subscription-funded eval backends: shell out to the `claude` or `codex` CLI.

Eval-only: never imported by the live loop (main.py uses judge.py /
judge_ollama.py). Each backend exposes the same two calls the eval runner
needs:

    vision_json(system, frames: list[Path], schema, name) -> dict
    complete_json(system, user, schema, name) -> dict

Both return the model's JSON object plus `_usage` = {} (subscription runs
report no token counts). The model's reply is free text, so the JSON is
extracted from it; one retry on a parse failure, then RuntimeError.

Every CLI process runs with a fresh empty temp dir as its cwd (never the
repo), so the agent can't pick up the repo's CLAUDE.md / AGENTS.md or read
project files. These numbers are a proxy for the live Anthropic path (no
forced tool call, free-text JSON, a different harness), not a measurement
of it.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT_S = 300
_FENCE = re.compile(r"```[a-zA-Z0-9_-]*")
_CODEX_HELP: dict[str, str] = {}  # cached `codex exec --help` text

_INSTALL = {
    "claude": ("Install Claude Code: `curl -fsSL https://claude.ai/install.sh | bash`, "
               "then run `claude` once to log in with your subscription."),
    "codex": "Install Codex: `npm i -g @openai/codex`, then run `codex login`.",
}


def extract_json(text: str) -> dict:
    """First balanced `{...}` in `text` that parses as a JSON object.
    Tolerates code fences and surrounding prose; raises ValueError if none."""
    text = _FENCE.sub("", text or "")
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


def _schema_tail(schema: dict, name: str) -> str:
    return (f"\n\nJSON schema for `{name}`:\n{json.dumps(schema, indent=2)}\n\n"
            "Reply with ONLY a JSON object matching this schema.")


def _resolve(exe: str) -> str:
    """The binary on PATH, else the native installer's ~/.local/bin copy
    (which the installer doesn't always put on PATH)."""
    if shutil.which(exe):
        return exe
    local = Path.home() / ".local" / "bin" / exe
    return str(local) if local.is_file() else exe


def _run(cmd: list[str], tool: str) -> subprocess.CompletedProcess:
    cmd = [_resolve(cmd[0]), *cmd[1:]]
    cwd = tempfile.mkdtemp(prefix="opener-eval-")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
                              cwd=cwd)
    except FileNotFoundError as e:
        raise RuntimeError(f"`{tool}` CLI not found. {_INSTALL[tool]}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"`{tool}` timed out after {TIMEOUT_S}s") from e
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"`{tool}` failed (exit {proc.returncode}): {tail}")
    return proc


def _with_retry(call, parse):
    """Run `call()` and `parse` its output; one retry on a parse failure."""
    last = None
    for _ in range(2):
        try:
            return parse(call())
        except ValueError as e:  # includes json.JSONDecodeError
            last = e
    raise RuntimeError(f"unparseable model output after retry: {last}") from last


class ClaudeCli:
    """`claude -p <user prompt> --output-format json --system-prompt <system>`
    (frames read via the Read tool). Model defaults to "sonnet"."""

    DEFAULT_MODEL = "sonnet"

    def __init__(self, model: str | None = None):
        self.model = model or self.DEFAULT_MODEL

    def _call(self, system: str, prompt: str, extra: list[str]) -> dict:
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--system-prompt", system, *extra, "--model", self.model]

        def parse(proc):
            outer = json.loads(proc.stdout)
            if not isinstance(outer, dict):
                raise ValueError("claude output is not a JSON object")
            if outer.get("is_error"):
                raise RuntimeError(f"claude reported an error: {str(outer.get('result'))[:300]}")
            return extract_json(str(outer.get("result") or ""))

        out = _with_retry(lambda: _run(cmd, "claude"), parse)
        out["_usage"] = {}
        return out

    def vision_json(self, system: str, frames: list[Path], schema: dict, name: str) -> dict:
        paths = [str(Path(f).resolve()) for f in frames]
        frames_dir = str(Path(paths[0]).parent)
        prompt = (f"Read these image files in order: {' '.join(paths)}"
                  + _schema_tail(schema, name))
        return self._call(system, prompt,
                          ["--allowedTools", "Read", "--add-dir", frames_dir])

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        return self._call(system, user + _schema_tail(schema, name), [])


def _codex_help() -> str:
    if "text" not in _CODEX_HELP:
        proc = _run(["codex", "exec", "--help"], "codex")
        _CODEX_HELP["text"] = (proc.stdout or "") + (proc.stderr or "")
    return _CODEX_HELP["text"]


def _codex_supports_output_schema() -> bool:
    return "--output-schema" in _codex_help()


class CodexCli:
    """`codex exec -i f1 -i f2 ... <prompt>`; JSON taken from stdout."""

    def __init__(self, model: str | None = None):
        self.model = model

    def _call(self, prompt: str, images: list[str], schema: dict) -> dict:
        def call():
            cmd = ["codex", "exec"]
            for img in images:
                cmd += ["-i", img]
            if self.model:
                cmd += ["-m", self.model]
            # The temp cwd is not a git repo; codex refuses that by default.
            if "--skip-git-repo-check" in _codex_help():
                cmd += ["--skip-git-repo-check"]
            if _codex_supports_output_schema():
                with tempfile.NamedTemporaryFile(
                        "w", suffix=".json", delete=False) as fh:
                    json.dump(schema, fh)
                schema_path = Path(fh.name)
                cmd += ["--output-schema", str(schema_path)]
            else:
                schema_path = None
            # `--` stops `-i` (which takes several values) from swallowing the
            # prompt as another image path.
            cmd += ["--", prompt]
            try:
                return _run(cmd, "codex")
            finally:
                if schema_path is not None:
                    schema_path.unlink(missing_ok=True)

        out = _with_retry(call, lambda proc: extract_json(proc.stdout))
        out["_usage"] = {}
        return out

    def vision_json(self, system: str, frames: list[Path], schema: dict, name: str) -> dict:
        images = [str(Path(f).resolve()) for f in frames]
        prompt = (f"{system}\n\nThe attached images are the profile screenshots, "
                  "in order from top to bottom." + _schema_tail(schema, name))
        return self._call(prompt, images, schema)

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        return self._call(f"{system}\n\n{user}" + _schema_tail(schema, name), [], schema)
