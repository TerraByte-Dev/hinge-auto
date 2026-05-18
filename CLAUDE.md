# Claude Code instructions

See [AGENTS.md](./AGENTS.md) — same content, kept canonical there so
Codex, Cursor, Aider, and other agents that respect the AGENTS.md
convention stay in sync with you.

## Claude-specific notes

- This repo defaults to the Anthropic backend (`JUDGE_BACKEND =
  "anthropic"` in `config.py`). If the user wants to use their Claude
  Max subscription's Agent SDK credit instead of API-key billing,
  that's a forthcoming option (Anthropic enabled subscription-funded
  programmatic credits on June 15, 2026). A third backend
  `judge_claude_code.py` shelling out to `claude -p` is the natural
  place to wire it; not implemented yet — flag as future work if asked.
- When walking the user through setup (see AGENTS.md Phase 1-5), use
  your file-editing and shell tools to make the config changes
  directly, rather than asking the user to copy-paste. Show the diff
  before writing.
- You can run `python main.py`, `python scan_self.py`, calibration
  scripts, etc. directly when the user asks. Watch the live output,
  surface anomalies, Ctrl-C if a decision or opener looks wrong.
