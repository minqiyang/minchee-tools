#!/usr/bin/env python3
"""Idle guard: a Claude Code hook that saves tokens after a long pause.

Claude Code caches the conversation prompt for a limited time (1 hour on a Claude subscription,
5 minutes by default on the API). After that, the next message re-reads the whole context as
uncached input. This hook cannot clear or compact anything itself (no hook can); it stops the
first message after a long pause and tells you, so you can choose /clear first.

Modes (the last command-line argument):
  stop    Stop hook: record when this session's last turn ended.
  prompt  UserPromptSubmit hook: if the last turn ended more than the idle limit ago and the context
          is at least the token threshold, block the first prompt once. A blocked prompt is never sent
          to the model, so it costs no tokens. Sending the same prompt again goes through. Slash
          commands are never blocked.
  clear   SessionStart hook (matcher "clear"): print a resume hint for the fresh session.

It checks elapsed time and the context size of the last request (read from the session transcript).
It does not inspect the cache itself or whether your work was saved; the message says so. If the
context size cannot be determined, the prompt is not blocked.

Configuration (all optional):
  IDLE_GUARD_DISABLE          set to 1 to turn the hook off in a session (for example, background agents
                              that receive prompts from a script rather than a person)
  IDLE_GUARD_SECONDS          idle limit in seconds (default 3600)
  IDLE_GUARD_MIN_TOKENS       only block when the context is at least this many tokens (default 100000;
                              0 blocks on idle time alone)
  IDLE_GUARD_STATE_DIR        state directory (default ~/.claude/state/idle_guard)
  IDLE_GUARD_MESSAGE_FILE     block message template (default file: ~/.config/idle-guard/message.txt).
                              Replaced: "{minutes}" idle minutes; "{limit}" idle
                              limit in minutes; "{tokens}" context size in k, one decimal (406.9k)
  IDLE_GUARD_CLEAR_HINT_FILE  text printed after /clear; otherwise <project>/.claude/idle_guard_clear_hint.md,
                              then ~/.config/idle-guard/clear_hint.txt, then a built-in default
"""
import json
import os
import re
import sys
import time

CONFIG_DIR = os.path.expanduser("~/.config/idle-guard")
PRUNE_SECONDS = 30 * 24 * 3600
TAIL_BYTES = 2 * 1024 * 1024

DEFAULT_MESSAGE = (
    "About {minutes} min since the last turn, past the {limit} min cache limit\n"
    "Context is about {tokens} tokens.\n"
    "Suggest: run /compact or /clear first.\n"
    "To continue anyway, send the same message again."
)

DEFAULT_CLEAR_HINT = (
    "The session was just cleared to save tokens. Before acting, rebuild the working state from disk: "
    "the memory index and the memory files it points to for the active task, then the project's handoff "
    "or status file if it has one. Check for running background work before starting anything new."
)


def _read_text(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _state_paths(state_dir, session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "unknown"))[:128] or "unknown"
    base = os.path.join(state_dir, safe)
    return base + ".last", base + ".warned"


def _prune(state_dir, now):
    try:
        names = os.listdir(state_dir)
    except OSError:
        return
    for name in names:
        if not name.endswith((".last", ".warned")):
            continue
        path = os.path.join(state_dir, name)
        try:
            if now - os.path.getmtime(path) > PRUNE_SECONDS:
                os.remove(path)
        except OSError:
            pass


def _context_tokens(transcript_path):
    """Context size of the session's latest main-thread request, or None if it cannot be determined.

    Reads the tail of the transcript and takes the usage of the newest non-sidechain assistant message:
    input + cache-read + cache-creation tokens. A compaction marker newer than that message makes the
    number stale, so it also returns None.
    """
    if not transcript_path:
        return None
    try:
        with open(os.path.expanduser(str(transcript_path)), "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("subtype") == "compact_boundary" or entry.get("isCompactSummary"):
            return None
        message = entry.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if entry.get("isSidechain") or not isinstance(usage, dict) or message.get("role") != "assistant":
            continue
        try:
            total = sum(int(usage.get(k) or 0) for k in
                        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        except (TypeError, ValueError):
            return None
        if total > 0:
            return total
    return None


def _format_k(tokens):
    return "{:.1f}k".format(tokens / 1000)


def _clear_hint(data):
    candidates = [os.environ.get("IDLE_GUARD_CLEAR_HINT_FILE")]
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd")
    if project:
        candidates.append(os.path.join(project, ".claude", "idle_guard_clear_hint.md"))
    candidates.append(os.path.join(CONFIG_DIR, "clear_hint.txt"))
    for path in candidates:
        if path:
            text = _read_text(path)
            if text:
                return text
    return DEFAULT_CLEAR_HINT


def main(argv, stdin, now=None):
    if os.environ.get("IDLE_GUARD_DISABLE") == "1":
        return ""
    now = time.time() if now is None else now
    mode = argv[-1] if len(argv) > 1 else ""
    try:
        data = json.load(stdin)
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    state_dir = os.path.expanduser(os.environ.get("IDLE_GUARD_STATE_DIR", "~/.claude/state/idle_guard"))
    try:
        idle_limit = float(os.environ.get("IDLE_GUARD_SECONDS", "3600"))
    except ValueError:
        idle_limit = 3600.0
    try:
        min_tokens = float(os.environ.get("IDLE_GUARD_MIN_TOKENS", "100000"))
    except ValueError:
        min_tokens = 100000.0
    last_path, warned_path = _state_paths(state_dir, data.get("session_id"))

    if mode == "stop":
        os.makedirs(state_dir, exist_ok=True)
        with open(last_path, "w") as f:
            f.write(str(int(now)))
        if os.path.exists(warned_path):
            os.remove(warned_path)
        _prune(state_dir, now)
        return ""

    if mode == "prompt":
        if str(data.get("prompt", "")).lstrip().startswith("/"):
            return ""
        try:
            with open(last_path) as f:
                last = int(f.read().strip())
        except (OSError, ValueError):
            return ""
        idle = now - last
        if idle <= idle_limit or os.path.exists(warned_path):
            return ""
        tokens = _context_tokens(data.get("transcript_path"))
        if min_tokens > 0 and (tokens is None or tokens < min_tokens):
            return ""
        os.makedirs(state_dir, exist_ok=True)
        open(warned_path, "w").close()
        template = (_read_text(os.environ.get("IDLE_GUARD_MESSAGE_FILE") or os.path.join(CONFIG_DIR, "message.txt"))
                    or DEFAULT_MESSAGE)
        reason = template.replace("{minutes}", str(int(idle // 60)))
        reason = reason.replace("{limit}", str(max(1, int(idle_limit / 60 + 0.5))))
        reason = reason.replace("{tokens}", _format_k(tokens) if tokens is not None else "unknown")
        return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)

    if mode == "clear":
        return _clear_hint(data)

    return ""


if __name__ == "__main__":
    out = main(sys.argv, sys.stdin)
    if out:
        print(out)
