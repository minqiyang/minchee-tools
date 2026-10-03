"""Tests for idle_guard.py. Run: python3 -m unittest test_idle_guard.py (standard library only)."""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import idle_guard


class IdleGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        self.env = mock.patch.dict(os.environ, {
            "IDLE_GUARD_STATE_DIR": self.state,
            "IDLE_GUARD_SECONDS": "3600",
            "IDLE_GUARD_MIN_TOKENS": "0",
            "IDLE_GUARD_DISABLE": "",
            "IDLE_GUARD_MESSAGE_FILE": os.path.join(self.tmp.name, "missing.txt"),
            "IDLE_GUARD_CLEAR_HINT_FILE": "",
            "CLAUDE_PROJECT_DIR": os.path.join(self.tmp.name, "project"),
        })
        self.env.start()
        self.config = mock.patch.object(idle_guard, "CONFIG_DIR", os.path.join(self.tmp.name, "config"))
        self.config.start()
        self.no_claude = mock.patch.object(idle_guard, "_claude_pid", return_value=None)
        self.no_claude.start()

    def tearDown(self):
        self.no_claude.stop()
        self.config.stop()
        self.env.stop()
        self.tmp.cleanup()

    def run_hook(self, mode, now, **fields):
        payload = {"session_id": "s1", **fields}
        return idle_guard.main(["idle_guard.py", mode], io.StringIO(json.dumps(payload)), now=now)

    def test_recent_turn_is_not_blocked(self):
        self.run_hook("stop", 1000)
        self.assertEqual(self.run_hook("prompt", 1000 + 3599, prompt="go"), "")

    def test_first_prompt_after_idle_is_blocked_once(self):
        self.run_hook("stop", 1000)
        out = json.loads(self.run_hook("prompt", 1000 + 4000, prompt="go"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("About 66 min", out["reason"])
        self.assertIn("60 min cache limit", out["reason"])
        self.assertIn("Suggest: run /clear first.", out["reason"])
        self.assertEqual(self.run_hook("prompt", 1000 + 4010, prompt="go"), "")

    def test_next_turn_resets_the_warning(self):
        self.run_hook("stop", 1000)
        self.run_hook("prompt", 5000, prompt="go")
        self.run_hook("stop", 5100)
        self.assertEqual(self.run_hook("prompt", 5200, prompt="go"), "")
        self.assertEqual(json.loads(self.run_hook("prompt", 5100 + 3601, prompt="go"))["decision"], "block")

    def test_slash_commands_and_new_sessions_pass(self):
        self.run_hook("stop", 1000)
        self.assertEqual(self.run_hook("prompt", 9000, prompt="  /clear"), "")
        self.assertEqual(idle_guard.main(["x", "prompt"], io.StringIO('{"session_id": "s2", "prompt": "go"}'),
                                         now=9000), "")

    def test_message_template_file(self):
        path = os.path.join(self.tmp.name, "msg.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("idle {minutes} min")
        os.environ["IDLE_GUARD_MESSAGE_FILE"] = path
        self.run_hook("stop", 0)
        self.assertEqual(json.loads(self.run_hook("prompt", 7200, prompt="go"))["reason"], "idle 120 min")

    def write_transcript(self, *entries):
        path = os.path.join(self.tmp.name, "transcript.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return path

    @staticmethod
    def assistant(tokens, sidechain=False):
        usage = {"input_tokens": 2, "cache_read_input_tokens": tokens - 12, "cache_creation_input_tokens": 10,
                 "output_tokens": 5}
        return {"type": "assistant", "isSidechain": sidechain, "message": {"role": "assistant", "usage": usage}}

    def test_token_threshold(self):
        os.environ["IDLE_GUARD_MIN_TOKENS"] = "100000"
        self.run_hook("stop", 1000)
        small = self.write_transcript(self.assistant(99999))
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go", transcript_path=small), "")
        big = self.write_transcript(self.assistant(100000))
        out = json.loads(self.run_hook("prompt", 9000, prompt="go", transcript_path=big))
        self.assertEqual(out["decision"], "block")

    def test_below_threshold_does_not_use_up_the_warning(self):
        os.environ["IDLE_GUARD_MIN_TOKENS"] = "100000"
        self.run_hook("stop", 1000)
        small = self.write_transcript(self.assistant(5000))
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go", transcript_path=small), "")
        big = self.write_transcript(self.assistant(150000))
        self.assertEqual(json.loads(self.run_hook("prompt", 9100, prompt="go", transcript_path=big))["decision"],
                         "block")

    def test_unknown_context_size_is_not_blocked(self):
        os.environ["IDLE_GUARD_MIN_TOKENS"] = "100000"
        self.run_hook("stop", 1000)
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go"), "")
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go",
                                       transcript_path=os.path.join(self.tmp.name, "nope.jsonl")), "")
        empty = self.write_transcript({"type": "user", "message": {"role": "user", "content": "hi"}})
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go", transcript_path=empty), "")

    def test_sidechain_is_ignored_and_latest_main_message_wins(self):
        os.environ["IDLE_GUARD_MIN_TOKENS"] = "100000"
        self.run_hook("stop", 1000)
        path = self.write_transcript(self.assistant(200000), self.assistant(5000),
                                     self.assistant(300000, sidechain=True))
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go", transcript_path=path), "")

    def test_compaction_marker_makes_the_size_unknown(self):
        os.environ["IDLE_GUARD_MIN_TOKENS"] = "100000"
        self.run_hook("stop", 1000)
        path = self.write_transcript(self.assistant(200000), {"type": "system", "subtype": "compact_boundary"})
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go", transcript_path=path), "")

    def test_tokens_placeholder(self):
        msg = os.path.join(self.tmp.name, "msg.txt")
        with open(msg, "w", encoding="utf-8") as f:
            f.write("{minutes} min, limit {limit}, {tokens}")
        os.environ["IDLE_GUARD_MESSAGE_FILE"] = msg
        os.environ["IDLE_GUARD_MIN_TOKENS"] = "100000"
        self.run_hook("stop", 0)
        path = self.write_transcript(self.assistant(123456))
        self.assertEqual(json.loads(self.run_hook("prompt", 7200, prompt="go", transcript_path=path))["reason"],
                         "120 min, limit 60, 123.5k")

    def test_tokens_shown_in_k_with_one_decimal(self):
        self.assertEqual(idle_guard._format_k(406923), "406.9k")
        self.assertEqual(idle_guard._format_k(100000), "100.0k")

    # --- notification timer ---

    def arm(self, transcript=None, last=0, session="s1", claude_pid=4242):
        """Write the state a stop hook would leave behind for a Claude Code process."""
        os.makedirs(self.state, exist_ok=True)
        with open(idle_guard._state_paths(self.state, session)[0], "w") as f:
            f.write(str(last))
        idle_guard._atomic_write(idle_guard._proc_path(self.state, claude_pid), json.dumps(
            {"session_id": session, "transcript_path": transcript, "timer_pid": None}))

    def run_timer(self, start=0, min_tokens=100000, on_sleep=None, claude_alive=True):
        clock = {"now": start}
        sent = []

        def sleep(seconds):
            clock["now"] += seconds
            if on_sleep:
                on_sleep(clock)

        with mock.patch.object(idle_guard, "_pid_alive", return_value=claude_alive):
            idle_guard.run_timer(4242, self.state, 3000, 3600, min_tokens, clock=lambda: clock["now"], sleep=sleep,
                                 notify=lambda title, body: sent.append((title, body)))
        return sent, clock["now"]

    def old_transcript(self, tokens):
        path = self.write_transcript(self.assistant(tokens))
        os.utime(path, (0, 0))
        return path

    def test_timer_notifies_at_the_notify_time(self):
        self.arm(self.old_transcript(406923))
        sent, waited_until = self.run_timer(start=100)
        self.assertEqual(waited_until, 3000)
        self.assertEqual(sent, [("Idle guard",
                                 "Cache expires in about 10 min. Context is about 406.9k tokens. Run /compact now?")])

    def test_timer_is_pushed_back_by_new_activity(self):
        self.arm(self.old_transcript(200000))

        def new_turn(clock):
            if clock["now"] == 3000:  # a turn ended just before the first notification time
                with open(idle_guard._state_paths(self.state, "s1")[0], "w") as f:
                    f.write("2900")

        sent, waited_until = self.run_timer(start=100, on_sleep=new_turn)
        self.assertEqual(waited_until, 5900)
        self.assertEqual(len(sent), 1)

    def test_timer_stays_quiet_when_it_should(self):
        small = self.old_transcript(5000)
        self.arm(small)
        self.assertEqual(self.run_timer(start=100)[0], [])  # context below the threshold
        self.arm(self.old_transcript(200000))
        self.assertEqual(self.run_timer(start=4000)[0], [])  # cache already expired
        self.assertEqual(self.run_timer(start=100, claude_alive=False)[0], [])  # Claude Code has exited
        os.remove(idle_guard._proc_path(self.state, 4242))
        self.assertEqual(self.run_timer(start=100)[0], [])  # session cleared

    def test_timer_skips_a_compacted_context(self):
        path = self.write_transcript(self.assistant(200000), {"type": "system", "subtype": "compact_boundary"})
        os.utime(path, (0, 0))
        self.arm(path)
        self.assertEqual(self.run_timer(start=100)[0], [])

    def test_stop_starts_one_timer_per_claude_process(self):
        spawned = []
        with mock.patch.object(idle_guard, "_claude_pid", return_value=4242), \
                mock.patch.object(idle_guard, "_spawn_timer", side_effect=lambda pid: spawned.append(pid) or 777), \
                mock.patch.object(idle_guard, "_is_timer", side_effect=lambda pid: pid == 777):
            self.run_hook("stop", 1000, transcript_path="/t.jsonl")
            self.run_hook("stop", 1100, session_id="s1", transcript_path="/t.jsonl")
        self.assertEqual(spawned, [4242])
        info = idle_guard._read_json(idle_guard._proc_path(self.state, 4242))
        self.assertEqual((info["session_id"], info["transcript_path"], info["timer_pid"]), ("s1", "/t.jsonl", 777))

    def test_notify_switch_and_unknown_claude_process(self):
        with mock.patch.object(idle_guard, "_spawn_timer") as spawn:
            with mock.patch.object(idle_guard, "_claude_pid", return_value=4242):
                os.environ["IDLE_GUARD_NOTIFY_SECONDS"] = "0"
                self.run_hook("stop", 1000)
                os.environ["IDLE_GUARD_NOTIFY_SECONDS"] = "3600"  # not before the cache expires
                self.run_hook("stop", 1000)
                del os.environ["IDLE_GUARD_NOTIFY_SECONDS"]
            self.run_hook("stop", 1000)  # no Claude Code process found (setUp patch)
            spawn.assert_not_called()

    def test_clear_stops_the_timer(self):
        self.arm()
        with mock.patch.object(idle_guard, "_claude_pid", return_value=4242):
            self.run_hook("clear", 0)
        self.assertFalse(os.path.exists(idle_guard._proc_path(self.state, 4242)))

    def test_claude_pid_walks_up_to_the_claude_process(self):
        lines = {"10": (20, "/bin/sh -c python3 idle_guard.py stop"), "20": (30, "claude --resume x"),
                 "30": (1, "-zsh")}
        self.no_claude.stop()
        try:
            with mock.patch.object(idle_guard.os, "getppid", return_value=10), \
                    mock.patch.object(idle_guard, "_process_line", side_effect=lambda pid: lines.get(str(pid))):
                self.assertEqual(idle_guard._claude_pid(), 20)
            with mock.patch.object(idle_guard.os, "getppid", return_value=30), \
                    mock.patch.object(idle_guard, "_process_line", side_effect=lambda pid: lines.get(str(pid))):
                self.assertIsNone(idle_guard._claude_pid())
        finally:
            self.no_claude.start()

    def test_notification_is_passed_as_arguments(self):
        with mock.patch.object(idle_guard.sys, "platform", "darwin"), \
                mock.patch.object(idle_guard.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(idle_guard.subprocess, "run") as run:
            idle_guard._notify('T"itle', 'body" & quit')
        command = run.call_args[0][0]
        self.assertEqual(command[0], "osascript")
        self.assertEqual(command[-2:], ['body" & quit', 'T"itle'])

    def test_clear_hint_priority(self):
        self.assertEqual(self.run_hook("clear", 0), idle_guard.DEFAULT_CLEAR_HINT)
        os.makedirs(idle_guard.CONFIG_DIR)
        with open(os.path.join(idle_guard.CONFIG_DIR, "clear_hint.txt"), "w") as f:
            f.write("user hint")
        self.assertEqual(self.run_hook("clear", 0), "user hint")
        project_hint = os.path.join(os.environ["CLAUDE_PROJECT_DIR"], ".claude", "idle_guard_clear_hint.md")
        os.makedirs(os.path.dirname(project_hint))
        with open(project_hint, "w") as f:
            f.write("project hint")
        self.assertEqual(self.run_hook("clear", 0), "project hint")

    def test_session_id_cannot_escape_the_state_dir(self):
        last, warned = idle_guard._state_paths(self.state, "../../etc/x")
        self.assertEqual(os.path.dirname(last), self.state)
        self.assertEqual(os.path.dirname(warned), self.state)

    def test_disable_switch(self):
        self.run_hook("stop", 1000)
        os.environ["IDLE_GUARD_DISABLE"] = "1"
        self.assertEqual(self.run_hook("prompt", 9000, prompt="go"), "")
        self.assertEqual(self.run_hook("clear", 9000), "")
        del os.environ["IDLE_GUARD_DISABLE"]
        self.assertEqual(json.loads(self.run_hook("prompt", 9000, prompt="go"))["decision"], "block")

    def test_bad_input_is_ignored(self):
        self.assertEqual(idle_guard.main(["x", "prompt"], io.StringIO("not json"), now=0), "")
        self.assertEqual(idle_guard.main(["x", "prompt"], io.StringIO("[1, 2]"), now=0), "")

    def test_old_state_is_pruned(self):
        self.run_hook("stop", 0)
        old = os.path.join(self.state, "old.last")
        open(old, "w").close()
        os.utime(old, (0, 0))
        self.run_hook("stop", idle_guard.PRUNE_SECONDS + 10)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(os.path.join(self.state, "s1.last")))


if __name__ == "__main__":
    unittest.main()
