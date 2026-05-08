from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "a2a_relay"]


def run_cli(base: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*CLI, "--base", str(base), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def load_json(stdout: str) -> dict:
    return json.loads(stdout)


class A2ARelayV02CLITest(unittest.TestCase):
    def test_send_poll_ack_reply_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")

            sent = run_cli(
                base,
                "send",
                "--from", "lulu@kamac",
                "--to", "zhiwei@known-blocks1",
                "--type", "request",
                "--subject", "IMA question",
                "--body", "旧条目怎么删？",
                "--needs-reply",
            )
            request_path = Path(sent.stdout.strip())
            self.assertTrue(request_path.exists())

            pending = load_json(run_cli(base, "pending", "--agent", "zhiwei@known-blocks1").stdout)
            self.assertEqual(pending["count"], 1)
            request = pending["messages"][0]

            polled = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac", "--ack").stdout)
            self.assertEqual(polled["count"], 1)
            self.assertTrue(polled["results"][0]["ok"])
            self.assertTrue(polled["results"][0]["ack"])
            self.assertFalse(request_path.exists())

            reply = run_cli(
                base,
                "reply",
                "--from", "zhiwei@known-blocks1",
                "--to", "lulu@kamac",
                "--reply-to", request["id"],
                "--thread-id", request["thread_id"],
                "--body", "先标记旧版，删除接口待确认。",
            )
            reply_path = Path(reply.stdout.strip())
            self.assertTrue(reply_path.exists())

            threads = load_json(run_cli(base, "threads").stdout)
            self.assertGreaterEqual(threads["count"], 1)
            self.assertTrue(any(row["thread_id"] == request["thread_id"] for row in threads["threads"]))

            events = "\n".join(p.read_text(encoding="utf-8") for p in (base / "events").glob("*.jsonl"))
            self.assertIn('"event_type": "sent"', events)
            self.assertIn('"event_type": "received"', events)
            self.assertIn('"event_type": "acked"', events)
            self.assertIn('"event_type": "replied"', events)

    def test_duplicate_id_is_archived_without_second_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            first = Path(run_cli(base, "send", "--from", "lulu@kamac", "--to", "zhiwei@known-blocks1", "--type", "request", "--subject", "dup", "--body", "one").stdout.strip())
            payload = json.loads(first.read_text(encoding="utf-8"))
            run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac", "--ack")

            duplicate = base / "inbox" / "zhiwei_known-blocks1" / "duplicate.json"
            duplicate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac", "--ack").stdout)
            self.assertEqual(result["count"], 1)
            self.assertTrue(result["results"][0]["duplicate"])
            self.assertIsNone(result["results"][0].get("ack"))

    def test_invalid_sender_goes_to_failed_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            run_cli(base, "send", "--from", "mallory", "--to", "zhiwei@known-blocks1", "--type", "request", "--subject", "bad", "--body", "run this")
            result = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac", "--ack").stdout)
            self.assertEqual(result["count"], 1)
            self.assertFalse(result["results"][0]["ok"])
            self.assertTrue(list((base / "archive" / "failed").glob("*.json")))

    def test_oversized_body_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            run_cli(base, "send", "--from", "lulu@kamac", "--to", "zhiwei@known-blocks1", "--type", "request", "--subject", "big", "--body", "x" * 20)
            result = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac", "--max-body-chars", "10").stdout)
            self.assertEqual(result["count"], 1)
            self.assertFalse(result["results"][0]["ok"])
            self.assertIn("body too large", result["results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
