# minchee-tools

Small tools and working rules for running AI coding agents, mainly Claude Code, cheaply and reliably.

| Tool | What it does |
|---|---|
| [claude-code/idle-guard](claude-code/idle-guard/README.md) | Hook that blocks the first message after an idle hour at zero token cost, so you can `/clear` before a full uncached re-read. |
| [claude-code/checkpoint-rule](claude-code/checkpoint-rule/README.md) | Rule that keeps an agent's resume state on disk at every pause, so `/clear` loses nothing. |

Each folder has its own README with install steps and limits.
