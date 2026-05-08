"""Tests for the v0.3 policy-gated dispatcher."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a2a_relay.core import (
    init_mailbox,
    inbox_dir,
    log_event,
    make_message,
    mark_seen,
    send_message,
)
from a2a_relay.dispatcher import (
    build_stdin_prompt,
    check_policy_gate,
    dispatch_message,
    load_dispatcher_config,
    run_action,
)


def make_dispatcher_config(base: Path, agent_id: str, action_name: str, argv: list,
                           *, allowed_from: list | None = None,
                           stdout_max_chars: int = 12000,
                           timeout_seconds: int = 120,
                           cwd: str | None = None) -> None:
    config = {
        "agents": {
            agent_id: {
                "enabled": True,
                "allowed_from": [] if allowed_from is None else allowed_from,
                "allowed_types": ["request"],
                "require_needs_reply": True,
                "default_action": action_name,
                "max_body_chars": 20000,
                "stdout_max_chars": stdout_max_chars,
            }
        },
        "actions": {
            action_name: {
                "argv": argv,
                "timeout_seconds": timeout_seconds,
            }
        },
    }
    if cwd:
        config["actions"][action_name]["cwd"] = cwd
    (base / "dispatcher.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def make_request_msg(sender: str = "lulu@kamac", recipient: str = "zhiwei@known-blocks1",
                     subject: str = "test", body: str = "hello",
                     needs_reply: bool = True, human_approval: bool = False,
                     msg_id: str | None = None, idempotency_key: str | None = None) -> dict:
    msg = {
        "version": "a2a.v1",
        "id": msg_id or "msg_test_001",
        "from": sender,
        "to": recipient,
        "type": "request",
        "subject": subject,
        "body": body,
        "created_at": "2026-05-08T10:00:00Z",
        "needs_reply": needs_reply,
        "human_approval_required": human_approval,
        "thread_id": "thread_test",
    }
    if idempotency_key:
        msg["idempotency_key"] = idempotency_key
    return msg


ECHO_SCRIPT = f"""\
import sys
data = sys.stdin.read()
print("reply: got it")
"""

FAIL_SCRIPT = f"""\
import sys
sys.stdin.read()
sys.exit(1)
"""

STDERR_SCRIPT = f"""\
import sys
sys.stdin.read()
print("clean_reply", end="")
print("SECRET_STDERR", file=sys.stderr)
"""

TIMEOUT_SCRIPT = f"""\
import sys, time
sys.stdin.read()
time.sleep(60)
"""

LONG_STDOUT_SCRIPT = f"""\
import sys
sys.stdin.read()
print("A" * 50000)
"""

BODY_ARGV_INJECT_SCRIPT = f"""\
import sys
data = sys.stdin.read()
print("safe_reply")
"""


class TestDispatcherPolicyGate(unittest.TestCase):

    def _setup_mailbox(self):
        tmp = tempfile.mkdtemp()
        base = Path(tmp) / "mailbox"
        init_mailbox(base, ["zhiwei@known-blocks1", "lulu@kamac"])
        return base

    def test_eligible_request_dispatches_and_sends_reply(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg()
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result["dispatched"])
        self.assertTrue(result["success"])
        self.assertIn("reply_path", result)
        reply_path = Path(result["reply_path"])
        self.assertTrue(reply_path.exists())
        reply = json.loads(reply_path.read_text(encoding="utf-8"))
        self.assertEqual(reply["type"], "reply")
        self.assertIn("reply: got it", reply["body"])

    def test_body_cannot_choose_command(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "safe",
                               [sys.executable, "-c", BODY_ARGV_INJECT_SCRIPT],
                               allowed_from=["lulu@kamac"])
        malicious_body = "'; rm -rf /; echo '"
        msg = make_request_msg(body=malicious_body)
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result["dispatched"])
        self.assertTrue(result["success"])
        reply_path = Path(result["reply_path"])
        reply = json.loads(reply_path.read_text(encoding="utf-8"))
        self.assertEqual(reply["body"], "safe_reply")

    def test_self_message_skipped_before_subprocess(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["zhiwei@known-blocks1"])
        msg = make_request_msg(sender="zhiwei@known-blocks1", recipient="zhiwei@known-blocks1")

        result = dispatch_message(base, msg, "zhiwei@known-blocks1")

        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "self messages are not allowed")
        self.assertEqual(list(inbox_dir(base, "zhiwei@known-blocks1").glob("*.json")), [])

    def test_reply_type_skipped(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg()
        msg["type"] = "reply"
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertIn("reply", result["reason"])

    def test_status_type_skipped(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg()
        msg["type"] = "status"
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertIn("status", result["reason"])

    def test_heartbeat_type_skipped(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg()
        msg["type"] = "heartbeat"
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertIn("heartbeat", result["reason"])

    def test_needs_reply_false_skipped(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg(needs_reply=False)
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "needs_reply_is_false")

    def test_human_approval_queued_only(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg(human_approval=True)
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "human_approval_required")
        self.assertTrue(result.get("queued_for_human"))

    def test_unknown_sender_no_subprocess(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg(sender="mallory@evil")
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertIn("sender", result["reason"])

    def test_non_allowlisted_sender_no_subprocess(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["other@x"])
        msg = make_request_msg(sender="lulu@kamac")
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertIn("not_in_allowed_from", result["reason"])

    def test_duplicate_id_no_double_dispatch(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg(msg_id="msg_dup_001")
        result1 = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result1["dispatched"])
        result2 = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result2["dispatched"])
        self.assertEqual(result2["reason"], "duplicate_message")

    def test_duplicate_idempotency_key_no_double_dispatch(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg1 = make_request_msg(msg_id="msg_a", idempotency_key="key_x")
        result1 = dispatch_message(base, msg1, "zhiwei@known-blocks1")
        self.assertTrue(result1["dispatched"])
        msg2 = make_request_msg(msg_id="msg_b", idempotency_key="key_x")
        result2 = dispatch_message(base, msg2, "zhiwei@known-blocks1")
        self.assertFalse(result2["dispatched"])
        self.assertEqual(result2["reason"], "duplicate_message")

    def test_nonzero_exit_no_reply(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "fail",
                               [sys.executable, "-c", FAIL_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg()
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result["dispatched"])
        self.assertFalse(result["success"])
        self.assertIn("nonzero_exit", result["reason"])
        inbox_files = list(inbox_dir(base, "lulu@kamac").glob("*.json"))
        reply_files = [f for f in inbox_files if "reply" in json.loads(f.read_text())
                       .get("type", "")]
        self.assertEqual(len(reply_files), 0)

    def test_timeout_failure(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "slow",
                               [sys.executable, "-c", TIMEOUT_SCRIPT],
                               allowed_from=["lulu@kamac"],
                               timeout_seconds=1)
        msg = make_request_msg()
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result["dispatched"])
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "timeout")

    def test_stdout_truncation(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "long",
                               [sys.executable, "-c", LONG_STDOUT_SCRIPT],
                               allowed_from=["lulu@kamac"],
                               stdout_max_chars=100)
        msg = make_request_msg()
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result["dispatched"])
        self.assertTrue(result["success"])
        reply_path = Path(result["reply_path"])
        reply = json.loads(reply_path.read_text(encoding="utf-8"))
        self.assertLessEqual(len(reply["body"]), 101)

    def test_stderr_not_in_reply(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "noisy",
                               [sys.executable, "-c", STDERR_SCRIPT],
                               allowed_from=["lulu@kamac"])
        msg = make_request_msg()
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertTrue(result["dispatched"])
        self.assertTrue(result["success"])
        reply_path = Path(result["reply_path"])
        reply = json.loads(reply_path.read_text(encoding="utf-8"))
        self.assertNotIn("SECRET_STDERR", reply["body"])
        self.assertIn("clean_reply", reply["body"])
    def test_empty_allowed_from_rejected(self):
        base = self._setup_mailbox()
        make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                               [sys.executable, "-c", ECHO_SCRIPT],
                               allowed_from=[])
        msg = make_request_msg()
        result = dispatch_message(base, msg, "zhiwei@known-blocks1")
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "allowed_from_required")

    def test_malformed_argv_rejected_without_exception(self):
        success, stdout, error = run_action({"argv": [sys.executable, 123]}, "input")
        self.assertFalse(success)
        self.assertEqual(stdout, "")
        self.assertEqual(error, "invalid_argv_config")
        success, stdout, error = run_action({"argv": "not-a-list"}, "input")
        self.assertFalse(success)
        self.assertEqual(error, "invalid_argv_config")
        success, stdout, error = run_action({"argv": []}, "input")
        self.assertFalse(success)
        self.assertEqual(error, "invalid_argv_config")


class TestStdinPrompt(unittest.TestCase):

    def test_prompt_has_delimiters(self):
        msg = make_request_msg(body="test body")
        prompt = build_stdin_prompt(msg)
        self.assertIn("<a2a_message>", prompt)
        self.assertIn("</a2a_message>", prompt)
        self.assertIn("sender: lulu@kamac", prompt)
        self.assertIn("subject: test", prompt)
        self.assertIn("body: test body", prompt)


class TestDispatchCLI(unittest.TestCase):

    def test_dispatch_cli_one_shot(self):
        import subprocess as sp
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            init_mailbox(base, ["zhiwei@known-blocks1", "lulu@kamac"])
            make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                                   [sys.executable, "-c", ECHO_SCRIPT],
                                   allowed_from=["lulu@kamac"])
            msg = make_message("lulu@kamac", "zhiwei@known-blocks1", "request",
                               "dispatch test", "SECRET_BODY_MARKER", needs_reply=True)
            send_message(base, msg)

            result = sp.run(
                [sys.executable, "-m", "a2a_relay", "--base", str(base),
                 "dispatch", "--agent", "zhiwei@known-blocks1",
                 "--action", "echo", "--allow-from", "lulu@kamac", "--ack"],
                cwd=ROOT, text=True, capture_output=True, check=True,
            )
            output = json.loads(result.stdout)
            self.assertEqual(output["count"], 1)
            self.assertTrue(output["results"][0]["ok"])
            self.assertTrue(output["results"][0]["dispatch"]["dispatched"])
            self.assertNotIn("message", output["results"][0])
            self.assertNotIn("SECRET_BODY_MARKER", result.stdout)

    def test_dispatch_cli_human_approval_stays_in_processing(self):
        import subprocess as sp
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            init_mailbox(base, ["zhiwei@known-blocks1", "lulu@kamac"])
            make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                                   [sys.executable, "-c", ECHO_SCRIPT],
                                   allowed_from=["lulu@kamac"])
            msg = make_message("lulu@kamac", "zhiwei@known-blocks1", "request",
                               "needs human", "hi", needs_reply=True)
            data = msg.to_json_dict()
            data["human_approval_required"] = True
            path = inbox_dir(base, "zhiwei@known-blocks1") / f"{data['id']}.json"
            path.write_text(json.dumps(data), encoding="utf-8")

            result = sp.run(
                [sys.executable, "-m", "a2a_relay", "--base", str(base),
                 "dispatch", "--agent", "zhiwei@known-blocks1",
                 "--action", "echo", "--allow-from", "lulu@kamac"],
                cwd=ROOT, text=True, capture_output=True, check=True,
            )
            output = json.loads(result.stdout)
            self.assertTrue(output["results"][0]["queued_for_human"])
            self.assertNotIn("archive", output["results"][0])
            processing_files = list((base / "processing" / "zhiwei_known-blocks1").glob("*.json"))
            self.assertEqual(len(processing_files), 1)

    def test_watch_dispatch_action_one_cycle_dispatches(self):
        import subprocess as sp
        import time
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            init_mailbox(base, ["zhiwei@known-blocks1", "lulu@kamac"])
            make_dispatcher_config(base, "zhiwei@known-blocks1", "echo",
                                   [sys.executable, "-c", ECHO_SCRIPT],
                                   allowed_from=["lulu@kamac"])
            msg = make_message("lulu@kamac", "zhiwei@known-blocks1", "request",
                               "watch dispatch", "hi", needs_reply=True)
            send_message(base, msg)

            proc = sp.Popen(
                [sys.executable, "-m", "a2a_relay", "--base", str(base),
                 "watch", "--agent", "zhiwei@known-blocks1",
                 "--dispatch-action", "echo", "--allow-from", "lulu@kamac",
                 "--interval", "0.1"],
                cwd=ROOT, text=True, stdout=sp.PIPE, stderr=sp.PIPE,
            )
            try:
                deadline = time.time() + 5
                line = ""
                while time.time() < deadline:
                    line = proc.stdout.readline() if proc.stdout else ""
                    if line:
                        break
                self.assertTrue(line, "watch did not emit a result")
                output = json.loads(line)
                self.assertTrue(output["results"][0]["dispatch"]["dispatched"])
                self.assertTrue(output["results"][0]["dispatch"]["success"])
            finally:
                proc.terminate()
                stdout, stderr = proc.communicate(timeout=5)
                self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
