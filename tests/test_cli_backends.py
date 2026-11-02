"""Eval backends: CLI wrappers (subprocess faked) and the vision_json
helpers in judge.py / judge_ollama.py (clients faked). No network."""
import json
import subprocess
from pathlib import Path

import pytest

import config
from evals.openers import cli_backends
from evals.openers.cli_backends import ClaudeCli, CodexCli, extract_json


# ---------------------------------------------------------------- extract_json

def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_prose_wrapped_and_nested():
    text = 'Sure! Here it is: {"a": {"b": [1, {"c": "x}y"}]}, "d": "{"} hope that helps {'
    assert extract_json(text) == {"a": {"b": [1, {"c": "x}y"}]}, "d": "{"}


def test_extract_json_skips_unparseable_brace():
    assert extract_json('note {not json} then {"ok": true}') == {"ok": True}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", "{broken"])
def test_extract_json_raises(text):
    with pytest.raises(ValueError):
        extract_json(text)


# ---------------------------------------------------------------- fakes

class FakeRun:
    """Stand-in for subprocess.run: returns queued (stdout, returncode)."""

    def __init__(self, outputs, help_text=""):
        self.outputs = list(outputs)
        self.help_text = help_text
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        if cmd[:3] == ["codex", "exec", "--help"]:
            return subprocess.CompletedProcess(cmd, 0, self.help_text, "")
        out, rc = self.outputs.pop(0)
        return subprocess.CompletedProcess(cmd, rc, out, "boom" if rc else "")

    @property
    def model_calls(self):
        return [c for c in self.calls if c[0][:3] != ["codex", "exec", "--help"]]


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}


def _frames(tmp_path):
    paths = []
    for i in range(2):
        p = tmp_path / f"frame_{i:02d}.png"
        p.write_bytes(b"png")
        paths.append(p)
    return paths


# ---------------------------------------------------------------- ClaudeCli

def test_claude_cli_vision_command_and_parse(tmp_path, monkeypatch):
    fake = FakeRun([(json.dumps({"type": "result", "result": 'ok ```json\n{"x": 3}\n```'}), 0)])
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    frames = _frames(tmp_path)
    out = ClaudeCli(None).vision_json("SYSTEM TEXT", frames, SCHEMA, "submit_decision")
    assert out == {"x": 3, "_usage": {}}
    cmd, kw = fake.calls[0]
    assert cmd[:2] == ["claude", "-p"]
    prompt = cmd[2]
    assert cmd[3:] == ["--output-format", "json", "--allowedTools", "Read",
                       "--add-dir", str(tmp_path.resolve())]
    assert kw["timeout"] == 300
    assert prompt.startswith("SYSTEM TEXT")
    order = [prompt.index(str(f.resolve())) for f in frames]
    assert order == sorted(order)
    assert "Read these image files in order:" in prompt
    assert json.dumps(SCHEMA, indent=2) in prompt
    assert prompt.rstrip().endswith("Reply with ONLY a JSON object matching this schema.")


def test_claude_cli_model_flag_and_complete_json(monkeypatch):
    fake = FakeRun([(json.dumps({"result": '{"x": 1}'}), 0)])
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    out = ClaudeCli("sonnet").complete_json("SYS", "USER BODY", SCHEMA, "select_opener")
    assert out == {"x": 1, "_usage": {}}
    cmd = fake.calls[0][0]
    assert cmd[-2:] == ["--model", "sonnet"]
    assert "--allowedTools" not in cmd and "--add-dir" not in cmd
    assert "SYS" in cmd[2] and "USER BODY" in cmd[2]


def test_claude_cli_retries_once_then_raises(monkeypatch):
    fake = FakeRun([(json.dumps({"result": "no json"}), 0),
                    ("not even json", 0)])
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    with pytest.raises(RuntimeError):
        ClaudeCli(None).complete_json("S", "U", SCHEMA, "n")
    assert len(fake.calls) == 2


def test_claude_cli_retry_succeeds(monkeypatch):
    fake = FakeRun([(json.dumps({"result": "sorry"}), 0),
                    (json.dumps({"result": '{"x": 2}'}), 0)])
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    assert ClaudeCli(None).complete_json("S", "U", SCHEMA, "n")["x"] == 2


def test_claude_cli_nonzero_exit_raises(monkeypatch):
    fake = FakeRun([("", 1)])
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="exit"):
        ClaudeCli(None).complete_json("S", "U", SCHEMA, "n")


def test_missing_binary_explains_install(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(cli_backends.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="claude.ai/install.sh"):
        ClaudeCli(None).complete_json("S", "U", SCHEMA, "n")
    with pytest.raises(RuntimeError, match="codex login"):
        CodexCli(None).complete_json("S", "U", SCHEMA, "n")


def test_timeout_raises_runtime_error(monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(cli_backends.subprocess, "run", slow)
    with pytest.raises(RuntimeError, match="timed out"):
        ClaudeCli(None).complete_json("S", "U", SCHEMA, "n")


# ---------------------------------------------------------------- CodexCli

@pytest.fixture(autouse=True)
def _reset_codex_help_cache():
    cli_backends._CODEX_HELP.clear()
    yield
    cli_backends._CODEX_HELP.clear()


def test_codex_cli_vision_command_without_output_schema(tmp_path, monkeypatch):
    fake = FakeRun([('thinking...\n{"x": 5}\n', 0)], help_text="Usage: codex exec [OPTIONS]")
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    frames = _frames(tmp_path)
    out = CodexCli("gpt-5").vision_json("SYS", frames, SCHEMA, "submit_decision")
    assert out == {"x": 5, "_usage": {}}
    cmd, kw = fake.model_calls[0]
    f1, f2 = (str(f.resolve()) for f in frames)
    assert cmd[:6] == ["codex", "exec", "-i", f1, "-i", f2]
    assert "--output-schema" not in cmd
    assert cmd[6:8] == ["-m", "gpt-5"]
    prompt = cmd[-1]
    assert prompt.startswith("SYS")
    assert "Reply with ONLY a JSON object matching this schema." in prompt
    assert kw["timeout"] == 300


def test_codex_cli_passes_output_schema_when_supported(tmp_path, monkeypatch):
    seen = {}

    fake = FakeRun([('{"x": 7}', 0)],
                   help_text="  --output-schema <FILE>  JSON Schema for the final message")

    def run(cmd, **kw):
        if "--output-schema" in cmd:
            seen["schema"] = json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text())
        return fake(cmd, **kw)

    monkeypatch.setattr(cli_backends.subprocess, "run", run)
    out = CodexCli(None).vision_json("SYS", _frames(tmp_path), SCHEMA, "n")
    assert out["x"] == 7
    cmd = fake.model_calls[0][0]
    assert "--output-schema" in cmd
    assert seen["schema"] == SCHEMA
    assert "-m" not in cmd


def test_codex_help_checked_once(tmp_path, monkeypatch):
    fake = FakeRun([('{"x": 1}', 0), ('{"x": 2}', 0)], help_text="")
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    cli = CodexCli(None)
    cli.complete_json("S", "U", SCHEMA, "n")
    CodexCli(None).complete_json("S", "U", SCHEMA, "n")
    helps = [c for c in fake.calls if c[0][:3] == ["codex", "exec", "--help"]]
    assert len(helps) == 1


def test_codex_cli_complete_json_has_no_images_and_retries(monkeypatch):
    fake = FakeRun([("nope", 0), ('{"x": 9}', 0)], help_text="")
    monkeypatch.setattr(cli_backends.subprocess, "run", fake)
    assert CodexCli(None).complete_json("S", "U", SCHEMA, "n")["x"] == 9
    for cmd, _ in fake.model_calls:
        assert "-i" not in cmd
    assert len(fake.model_calls) == 2


# ---------------------------------------------------------------- judge.vision_json

class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_anthropic(monkeypatch, tool_input, name="submit_decision"):
    import anthropic
    sent = {}

    class FakeMessages:
        def create(self, **kw):
            sent.update(kw)
            return _Blk(content=[_Blk(type="tool_use", name=name, input=tool_input)],
                        usage=_Blk(input_tokens=10, output_tokens=5,
                                   cache_creation_input_tokens=None,
                                   cache_read_input_tokens=2),
                        stop_reason="tool_use")

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return sent


def test_judge_vision_json(monkeypatch):
    import judge
    sent = _fake_anthropic(monkeypatch, {"x": 1}, name="tool_x")
    out = judge.vision_json("SYS", [b"a", b"b"], SCHEMA, "tool_x")
    assert out == {"x": 1, "_usage": {"input_tokens": 10, "output_tokens": 5,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 2}}
    assert sent["tools"][0]["name"] == "tool_x"
    assert sent["tools"][0]["input_schema"] is SCHEMA
    assert sent["tool_choice"] == {"type": "tool", "name": "tool_x"}
    assert sent["system"][0]["text"] == "SYS"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["output_config"] == {"effort": config.EFFORT}
    assert [b["type"] for b in sent["messages"][0]["content"]] == ["image", "image", "text"]


def test_judge_judge_uses_vision_json_unchanged(monkeypatch):
    import judge
    from judge_common import DECIDE_INPUT_SCHEMA, build_system_prompt
    sent = _fake_anthropic(monkeypatch, {"name": "ana", "decision": "like",
                                         "confidence": "high", "reasoning": "r",
                                         "message": "hi", "skip_reason": "none",
                                         "message_archetype": "question",
                                         "premade_id": "", "prompt_referenced": ""})
    d = judge.judge([b"a"])
    assert d.decision == "like" and d.name == "ana"
    assert d.usage["input_tokens"] == 10
    assert sent["tools"][0]["name"] == "submit_decision"
    assert sent["tools"][0]["description"] == "Submit a like/skip decision for this Hinge profile."
    assert sent["tools"][0]["input_schema"] is DECIDE_INPUT_SCHEMA
    assert sent["system"][0]["text"] == build_system_prompt()
    assert sent["max_tokens"] == 2000
    assert sent["model"] == config.MODEL
    assert "Decide whether to like or skip." in sent["messages"][0]["content"][-1]["text"]


def test_judge_vision_json_no_tool_raises(monkeypatch):
    import judge
    _fake_anthropic(monkeypatch, {"x": 1}, name="other")
    with pytest.raises(RuntimeError, match="tool_use"):
        judge.vision_json("S", [b"a"], SCHEMA, "tool_x")


def test_ollama_vision_json_tool_call(monkeypatch):
    import judge_ollama
    sent = {}

    class FakeClient:
        def chat(self, **kw):
            sent.update(kw)
            return {"message": {"tool_calls": [{"function": {
                "name": "tool_x", "arguments": '{"x": 4}'}}]},
                "prompt_eval_count": 7, "eval_count": 3}

    monkeypatch.setattr(judge_ollama, "_client", lambda: FakeClient())
    out = judge_ollama.vision_json("SYS", [b"a"], SCHEMA, "tool_x")
    assert out == {"x": 4, "_usage": {"input_tokens": 7, "output_tokens": 3,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 0}}
    assert sent["tools"][0]["function"]["name"] == "tool_x"
    assert sent["tools"][0]["function"]["parameters"] is SCHEMA
    assert sent["messages"][0] == {"role": "system", "content": "SYS"}
    assert sent["messages"][1]["images"] == ["YQ=="]


def test_ollama_vision_json_content_fallback_and_judge(monkeypatch):
    import judge_ollama

    class FakeClient:
        def chat(self, **kw):
            return {"message": {"content": 'here ```{"name": "bo", "decision": "skip"}```'}}

    monkeypatch.setattr(judge_ollama, "_client", lambda: FakeClient())
    assert judge_ollama.vision_json("S", [b"a"], SCHEMA, "n")["name"] == "bo"
    d = judge_ollama.judge([b"a"])
    assert d.name == "bo" and d.decision == "skip"


def test_ollama_vision_json_nothing_raises(monkeypatch):
    import judge_ollama

    class FakeClient:
        def chat(self, **kw):
            return {"message": {"content": "no idea"}}

    monkeypatch.setattr(judge_ollama, "_client", lambda: FakeClient())
    with pytest.raises(RuntimeError):
        judge_ollama.vision_json("S", [b"a"], SCHEMA, "n")
