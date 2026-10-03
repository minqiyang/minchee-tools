#!/usr/bin/env python3
"""Idle guard: a Claude Code hook that saves tokens after a long pause.

Claude Code caches the conversation prompt for a limited time (1 hour on a Claude subscription,
5 minutes by default on the API). After that, the next message re-reads the whole context as
uncached input. This hook cannot clear or compact anything itself (no hook can); it works in two steps:
  1. Before the cache expires, a notification tells you, so you can /compact while the cache is still warm
     (compacting reads the context too, and a warm read is far cheaper than a cold one).
  2. If the cache expires anyway, it stops the first message after the pause and tells you, so you can
     choose /clear first.

Modes (the last command-line argument):
  stop    Stop hook: record when this session's last turn ended, and make sure one background timer is
          running for this Claude Code process (see "timer").
  prompt  UserPromptSubmit hook: if the last turn ended more than the idle limit ago and the context
          is at least the token threshold, block the first prompt once. A blocked prompt is never sent
          to the model, so it costs no tokens. Sending the same prompt again goes through. Slash
          commands are never blocked.
  clear   SessionStart hook (matcher "clear"): print a resume hint for the fresh session, and stop the
          notification timer (the cleared session has nothing to compact).
  timer   Started by the stop hook, not by Claude Code. Sleeps until the notification time, then sends one
          desktop notification and exits. New activity (a new turn, or any write to the transcript) pushes the
          time back. It exits when the Claude Code process ends, the session is cleared, the context is
          below the threshold or was compacted, or the cache has already expired.

It checks elapsed time and the context size of the last request (read from the session transcript).
It does not inspect the cache itself or whether your work was saved. If the context size cannot be
determined, nothing is blocked and no notification is sent.

Configuration (all optional):
  IDLE_GUARD_DISABLE          set to 1 to turn the hook off in a session (for example, background agents
                              that receive prompts from a script rather than a person)
  IDLE_GUARD_SECONDS          idle limit in seconds (default 3600)
  IDLE_GUARD_NOTIFY_SECONDS   send the notification after this much idle time (default 3000, i.e. 50 minutes);
                              0 turns the notification off
  IDLE_GUARD_MIN_TOKENS       only block or notify when the context is at least this many tokens (default
                              100000; 0 acts on idle time alone)
  IDLE_GUARD_STATE_DIR        state directory (default ~/.claude/state/idle_guard)
  IDLE_GUARD_MESSAGE_FILE     block message template (default file: ~/.config/idle-guard/message.txt).
                              Replaced: "{minutes}" idle minutes; "{limit}" idle limit in minutes;
                              "{tokens}" context size in k, one decimal (406.9k)
  IDLE_GUARD_CLEAR_HINT_FILE  text printed after /clear; otherwise <project>/.claude/idle_guard_clear_hint.md,
                              then ~/.config/idle-guard/clear_hint.txt, then a built-in default
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

CONFIG_DIR = os.path.expanduser("~/.config/idle-guard")
PRUNE_SECONDS = 30 * 24 * 3600
TAIL_BYTES = 2 * 1024 * 1024

DEFAULT_MESSAGE = (
    "About {minutes} min since the last turn, past the {limit} min cache limit\n"
    "Context is about {tokens} tokens.\n"
    "Suggest: run /clear first."
)

NOTIFY_TITLE = "Idle guard"
NOTIFY_MESSAGE = "Cache expires in about {remaining} min. Context is about {tokens} tokens. Run /compact now?"

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


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _state_paths(state_dir, session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "unknown"))[:128] or "unknown"
    base = os.path.join(state_dir, safe)
    return base + ".last", base + ".warned"


def _proc_path(state_dir, claude_pid):
    return os.path.join(state_dir, "proc-%d.json" % claude_pid)


def _prune(state_dir, now):
    try:
        names = os.listdir(state_dir)
    except OSError:
        return
    for name in names:
        if not name.endswith((".last", ".warned", ".json")):
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


def _process_line(pid):
    """(parent pid, command line) of a process, or None."""
    try:
        out = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    return int(parts[0]), parts[1]


def _claude_pid():
    """Pid of the Claude Code process that launched this hook (nearest ancestor run as `claude`), or None."""
    pid = os.getppid()
    for _ in range(6):
        if pid <= 1:
            return None
        line = _process_line(pid)
        if line is None:
            return None
        parent, command = line
        if os.path.basename(command.split(None, 1)[0]) == "claude":
            return pid
        pid = parent
    return None


def _pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 1:
            return False
        os.kill(pid, 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _is_timer(pid):
    if not _pid_alive(pid):
        return False
    line = _process_line(pid)
    return line is not None and "idle_guard" in line[1]


def _spawn_timer(claude_pid):
    """Start the detached timer process; returns its pid, or None."""
    try:
        child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "timer"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        child.stdin.write(json.dumps({"claude_pid": claude_pid}).encode())
        child.stdin.close()
    except OSError:
        return None
    return child.pid


def _arm_timer(state_dir, data):
    """Record which session this Claude Code process is on, and start the timer unless one is running."""
    claude_pid = _claude_pid()
    if claude_pid is None:
        return
    path = _proc_path(state_dir, claude_pid)
    old = _read_json(path) or {}
    record = {"session_id": data.get("session_id"), "transcript_path": data.get("transcript_path"),
              "timer_pid": old.get("timer_pid")}
    _atomic_write(path, json.dumps(record))
    if _is_timer(record["timer_pid"]):
        return
    record["timer_pid"] = _spawn_timer(claude_pid)
    _atomic_write(path, json.dumps(record))


def _disarm_timer(state_dir):
    claude_pid = _claude_pid()
    if claude_pid is not None:
        try:
            os.remove(_proc_path(state_dir, claude_pid))
        except OSError:
            pass


def _notify(title, body):
    if sys.platform == "darwin" and shutil.which("osascript"):
        command = ["osascript", "-e", "on run argv", "-e",
                   "display notification (item 1 of argv) with title (item 2 of argv)", "-e", "end run",
                   body, title]
    elif shutil.which("notify-send"):
        command = ["notify-send", title, body]
    else:
        return
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def run_timer(claude_pid, state_dir, notify_after, idle_limit, min_tokens,
              clock=time.time, sleep=time.sleep, notify=_notify):
    """Wait until the session has been idle for notify_after seconds, then notify once. See the docstring."""
    while True:
        if not _pid_alive(claude_pid):
            return
        info = _read_json(_proc_path(state_dir, claude_pid))
        if info is None:
            return
        last_path, _ = _state_paths(state_dir, info.get("session_id"))
        try:
            with open(last_path) as f:
                last = float(f.read().strip())
        except (OSError, ValueError):
            return
        try:
            last = max(last, os.path.getmtime(os.path.expanduser(str(info.get("transcript_path")))))
        except (OSError, TypeError):
            pass
        now = clock()
        if now - last >= idle_limit:
            return
        due = last + notify_after
        if now < due:
            sleep(due - now)
            continue
        break
    tokens = _context_tokens(info.get("transcript_path"))
    if min_tokens > 0 and (tokens is None or tokens < min_tokens):
        return
    remaining = max(1, int((last + idle_limit - now) / 60 + 0.5))
    body = NOTIFY_MESSAGE.replace("{remaining}", str(remaining))
    body = body.replace("{tokens}", _format_k(tokens) if tokens is not None else "unknown")
    notify(NOTIFY_TITLE, body)


def _prompt_reason(data, state_dir, idle_limit, min_tokens, now):
    """The block message for this prompt, or "" to let it through."""
    if str(data.get("prompt", "")).lstrip().startswith("/"):
        return ""
    last_path, warned_path = _state_paths(state_dir, data.get("session_id"))
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
    idle_limit = _float_env("IDLE_GUARD_SECONDS", 3600)
    min_tokens = _float_env("IDLE_GUARD_MIN_TOKENS", 100000)
    notify_after = _float_env("IDLE_GUARD_NOTIFY_SECONDS", 3000)

    if mode == "stop":
        last_path, warned_path = _state_paths(state_dir, data.get("session_id"))
        os.makedirs(state_dir, exist_ok=True)
        with open(last_path, "w") as f:
            f.write(str(int(now)))
        if os.path.exists(warned_path):
            os.remove(warned_path)
        _prune(state_dir, now)
        if 0 < notify_after < idle_limit:
            _arm_timer(state_dir, data)
        return ""

    if mode == "prompt":
        return _prompt_reason(data, state_dir, idle_limit, min_tokens, now)

    if mode == "clear":
        _disarm_timer(state_dir)
        return _clear_hint(data)

    if mode == "timer":
        try:
            claude_pid = int(data.get("claude_pid"))
        except (TypeError, ValueError):
            return ""
        run_timer(claude_pid, state_dir, notify_after, idle_limit, min_tokens)
        return ""

    return ""


if __name__ == "__main__":
    out = main(sys.argv, sys.stdin)
    if out:
        print(out)
