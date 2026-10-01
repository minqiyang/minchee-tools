# Checkpoint Rule

A working rule for long-running Claude Code sessions, especially a coordinator session that dispatches other
agents: keep the resume state on disk, so `/clear` loses nothing and a fresh session costs a few tens of
thousands of tokens instead of a full re-read.

It pairs with the [idle guard](../../hooks/idle-guard/README.md), which tells you when a pause has made the next message
expensive.

## The rule

At every pause point (waiting for a review, a CI run, or a background agent; a step finished; the end of a
working session), before ending the turn, the agent:

1. Writes the exact resume state to its memory files: the running tabs or agents and what each one is doing,
   which report or check it is waiting for and where it will appear, the decisions made so far, and the next
   action.
2. Tells you in one line that it is now safe to `/clear`.

After `/clear`, the fresh session loads the memory index automatically, reads the memory files and the
project's handoff file, checks for running work, and continues.

## Why it works

- `/clear` reads none of the old conversation. Compacting after the cache has expired reads all of it once.
- A fresh session's cost is the system prompt, the memory index, and the files it reads to resume. In
  practice that is about 30K to 50K tokens, against hundreds of thousands for a long conversation.
- What survives `/clear` is only what is on disk. The rule makes "on disk" true at every pause, so the choice
  to clear is safe whenever the agent has said so.

## Add it to your setup

Put the rule where your agent always reads it, such as `~/.claude/CLAUDE.md`, a project `CLAUDE.md`, or a
memory file:

```markdown
## Checkpoints

At every pause point (waiting on a review, CI, or a background agent; a step finished), before ending the
turn, write the exact resume state into memory (running agents and what each does, pending reports and their
paths, decisions so far, next action), then tell me in one line that it is safe to /clear. After a /clear,
rebuild the state from memory and the handoff file and check running work before starting anything new.
```

## Limits

- The agent cannot clear or compact the session itself, and cannot act before your next message arrives.
- The rule is only as good as the written state. If a turn ends without a checkpoint, do not clear.
- Lowering the automatic compaction point (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`) is another option; it trades
  detail for a smaller worst case. This setup leaves it at the default.
