# Idle Guard

A Claude Code hook that stops you from paying for a full uncached re-read of a long conversation after a
long pause, at zero token cost.

## Why

- Claude Code caches the conversation prompt. The cache lasts 1 hour on a Claude subscription and 5 minutes
  by default on the API ([prompt caching](https://code.claude.com/docs/en/prompt-caching.md)).
- After it expires, your next message re-reads the whole context as uncached input. With a 300K-token
  conversation, that one message is expensive.
- Compacting after the cache has expired does not avoid this: the summary request reads the full history
  once. `/clear` reads none of it.
- Neither the model nor a hook can run `/compact` or `/clear`. Only you can
  ([hooks guide](https://code.claude.com/docs/en/hooks-guide.md)).
- A `UserPromptSubmit` hook that blocks a prompt keeps it from the model entirely: no API call, no tokens
  ([hooks reference](https://code.claude.com/docs/en/hooks.md)).

So the guard does the one useful thing a hook can do: it stops the first message after a long pause and
tells you, so you can decide to `/clear` first.

These facts were checked against the official docs on 2026-09-30. Recheck them if Claude Code changes.

## What it does

| Event | Behavior |
|---|---|
| `Stop` | Records when the session's last turn ended. Any turn refreshes the cache, including turns started by background-task notifications. |
| `UserPromptSubmit` | If the last turn ended more than 1 hour ago, blocks your first message once and shows why. Send the same message again to go through. Slash commands are never blocked. |
| `SessionStart` (matcher `clear`) | After `/clear`, tells the fresh session to rebuild its state from disk (memory files, handoff file). |

It checks elapsed time only. It does not inspect the cache, the context size, or whether your work was
saved, and its message says so. Whether `/clear` is safe is your call; the
[checkpoint rule](../../rules/checkpoint-rule/README.md) makes that call easy.

## Install

```bash
git clone https://github.com/minqiyang/minchee-tools.git
cd minchee-tools/claude-code/hooks/idle-guard
./install.sh ~/.claude/settings.json                         # all projects
# or: ./install.sh /path/to/project/.claude/settings.local.json   # one project, not committed
```

The installer copies `idle_guard.py` to `~/.claude/hooks/`, backs up the settings file to
`~/.claude/state/idle_guard/backups/`, and appends three hooks without touching existing ones. Running it
twice adds nothing. It needs `python3` and `jq`. If a running session does not pick up the change, open
`/hooks` once or restart Claude Code.

To uninstall, remove the three entries whose command contains `idle_guard.py` from the settings file.

## Configure (optional)

| Setting | Default |
|---|---|
| `IDLE_GUARD_DISABLE` | unset; `1` turns the hook off for that session |
| `IDLE_GUARD_SECONDS` | `3600` |
| `IDLE_GUARD_STATE_DIR` | `~/.claude/state/idle_guard` |
| Block message | `IDLE_GUARD_MESSAGE_FILE`, else `~/.config/idle-guard/message.txt`, else built-in English. `{minutes}` is replaced. |
| Hint after `/clear` | `IDLE_GUARD_CLEAR_HINT_FILE`, else `<project>/.claude/idle_guard_clear_hint.md`, else `~/.config/idle-guard/clear_hint.txt`, else built-in. |

Environment variables can be set in the `env` block of a settings file. A message file is the easy way to
use your own language. A per-project hint file can name that project's handoff file and tools.

State files older than 30 days are pruned automatically.

## Background agents

If a script sends prompts to a Claude Code session (for example, a coordinator that dispatches work to worker
sessions), a worker idle for over an hour would have its next prompt blocked once. Start such sessions with
`IDLE_GUARD_DISABLE=1` in their environment, or start a fresh session instead of reusing one idle that long. A fresh
session has no state file and is never blocked.

## Test

```bash
python3 -m unittest test_idle_guard.py
```
