# Claude Code hooks

Scripts that Claude Code runs on hook events (`Stop`, `UserPromptSubmit`, `SessionStart`, `PreToolUse`, and
others), configured under `"hooks"` in a settings file. See the
[hooks reference](https://code.claude.com/docs/en/hooks.md).

Conventions for a hook in this folder:

- One folder per hook: the script, an `install.sh`, tests, and a README.
- `install.sh <settings.json>` copies the script to `~/.claude/hooks/`, backs up the settings file, and appends its
  entries without touching other hooks. Running it twice changes nothing.
- The hook does nothing and exits 0 on input it does not understand, so it can never break a session.
- Configuration comes from environment variables or files under `~/.config/<hook-name>/`, never from edits to the
  script.

| Hook | Events | Purpose |
|---|---|---|
| [idle-guard](idle-guard/README.md) | `Stop`, `UserPromptSubmit`, `SessionStart` | Warn before an expensive uncached re-read after an idle hour. |
