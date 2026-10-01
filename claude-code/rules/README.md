# Working rules

Rules are short instructions for an agent to follow, meant to be pasted into `CLAUDE.md` (global or per project),
an `AGENTS.md`, or a memory file. Each rule has its own folder with a README that states the rule, why it works,
the exact text to paste, and its limits.

| Rule | Purpose |
|---|---|
| [checkpoint-rule](checkpoint-rule/README.md) | Keep resume state on disk at every pause, so `/clear` loses nothing. |
