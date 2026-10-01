# minchee-tools

Small tools and working rules for running AI coding agents, mainly Claude Code, cheaply and reliably.

## Tools

| Tool | Category | What it does |
|---|---|---|
| [idle-guard](claude-code/hooks/idle-guard/README.md) | Claude Code hook | Blocks the first message after an idle hour at zero token cost, so you can `/clear` before a full uncached re-read. |
| [checkpoint-rule](claude-code/rules/checkpoint-rule/README.md) | Claude Code rule | Keeps an agent's resume state on disk at every pause, so `/clear` loses nothing. |

## Layout

```text
claude-code/          tools for Claude Code
  hooks/              hook scripts and their installers (settings.json "hooks")
  skills/             skills: one folder per skill with a SKILL.md (~/.claude/skills/<name>/)
  agents/             subagent definitions (~/.claude/agents/<name>.md)
  rules/              working rules to paste into CLAUDE.md or memory
codex/                tools for the Codex CLI (AGENTS.md rules, config snippets)
scripts/              standalone command-line utilities, and this repository's own scripts
docs/                 conventions for this repository
```

Each tool lives in its own folder inside one category, with a README and, where it has code, tests next to it.
Each category folder has a README that says what belongs there and how its tools are installed. To add a tool,
follow [docs/adding-a-tool.md](docs/adding-a-tool.md).

## Tests

```bash
scripts/run-tests.sh
```

It runs every `test_*.py` file in the repository. GitHub Actions runs it on each push and pull request.

## License

[MIT](LICENSE).
