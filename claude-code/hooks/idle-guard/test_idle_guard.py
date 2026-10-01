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

    def tearDown(self):
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
        self.assertIn("/compact", out["reason"])
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
