#!/usr/bin/env bash
# Install the idle guard hook into one Claude Code settings file.
#
#   ./install.sh ~/.claude/settings.json                  # every project
#   ./install.sh /path/to/project/.claude/settings.local.json   # one project, not committed
#
# Copies idle_guard.py to ~/.claude/hooks/, backs up the settings file to
# ~/.claude/state/idle_guard/backups/, and appends three hook entries. Existing hooks are kept,
# and running it twice adds nothing. Requires python3 and jq.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <settings.json>" >&2
  exit 2
fi
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

settings="$1"
here="$(cd "$(dirname "$0")" && pwd)"
hook_dir="$HOME/.claude/hooks"
backup_dir="$HOME/.claude/state/idle_guard/backups"
script="$hook_dir/idle_guard.py"

mkdir -p "$hook_dir" "$backup_dir" "$(dirname "$settings")"
cp "$here/idle_guard.py" "$script"
chmod +x "$script"

if [ -f "$settings" ]; then
  backup="$backup_dir/$(basename "$settings").$(date +%Y%m%d%H%M%S).bak"
  cp "$settings" "$backup"
  echo "backup: $backup"
else
  echo '{}' > "$settings"
fi

cmd="python3 $script"
tmp="$(mktemp)"
jq --arg cmd "$cmd" '
  def add(event; entry):
    if any(.hooks[event][]?.hooks[]?; .command == entry.hooks[0].command) then .
    else .hooks[event] = ((.hooks[event] // []) + [entry]) end;
  .hooks = (.hooks // {})
  | add("Stop"; {"hooks": [{"type": "command", "command": ($cmd + " stop"), "timeout": 10}]})
  | add("UserPromptSubmit"; {"hooks": [{"type": "command", "command": ($cmd + " prompt"), "timeout": 10}]})
  | add("SessionStart"; {"matcher": "clear", "hooks": [{"type": "command", "command": ($cmd + " clear"), "timeout": 10}]})
' "$settings" > "$tmp"
mv "$tmp" "$settings"

echo "installed: $script"
echo "hooks in $settings:"
jq -r '.hooks | to_entries[] | .key as $k | .value[] | .hooks[] | select(.command | test("idle_guard")) | "  \($k): \(.command)"' "$settings"
echo "If a running session does not pick it up, open /hooks once or restart Claude Code."
