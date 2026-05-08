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
    def test_task_send_creates_policy_bounded_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lancha@macmini")

            sent = run_cli(
                base,
                "task",
                "send",
                "--from", "zhiwei@known-blocks1",
                "--to", "lancha@macmini",
                "--title", "Check local database reachability",
                "--context", "Known symptom: timeout from public host.",
                "--constraint", "Read-only checks only.",
                "--require-output", "summary",
                "--require-output", "commands run",
                "--capability", "terminal",
                "--capability", "database_read",
                "--profile", "read-only-fast",
                "--approval-required",
            )
            msg_path = Path(sent.stdout.strip())
            msg = json.loads(msg_path.read_text(encoding="utf-8"))

            self.assertEqual(msg["type"], "request")
            self.assertTrue(msg["needs_reply"])
            self.assertTrue(msg["human_approval_required"])
            self.assertEqual(msg["capabilities_requested"], ["terminal", "database_read"])
            self.assertIn("task_zhiwei_known-blocks1_to_lancha_macmini", msg["thread_id"])
            self.assertIn("# Task", msg["body"])
            self.assertIn("## Context", msg["body"])
            self.assertIn("Known symptom: timeout from public host.", msg["body"])
            self.assertIn("## Constraints", msg["body"])
            self.assertIn("Read-only checks only.", msg["body"])
            self.assertIn("profile: read-only-fast", msg["body"])
            self.assertIn("human_approval_required: true", msg["body"])
            self.assertIn("1. summary", msg["body"])
            self.assertIn("2. commands run", msg["body"])

    def test_task_send_defaults_to_safe_read_only_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lancha@macmini")

            sent = run_cli(
                base,
                "task",
                "send",
                "--from", "zhiwei@known-blocks1",
                "--to", "lancha@macmini",
                "--title", "Inspect service health",
            )
            msg = json.loads(Path(sent.stdout.strip()).read_text(encoding="utf-8"))

            self.assertEqual(msg["capabilities_requested"], [])
            self.assertFalse(msg["human_approval_required"])
            self.assertTrue(msg["needs_reply"])
            self.assertIn("No additional context provided.", msg["body"])
            self.assertIn("Do not make destructive changes.", msg["body"])
            self.assertIn("Do not expose secrets in the reply.", msg["body"])
            self.assertIn("If write/restart/delete/migration is needed", msg["body"])
            self.assertIn("whether human approval is needed", msg["body"])

    def test_task_send_custom_constraints_append_to_safety_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lancha@macmini")

            sent = run_cli(
                base,
                "task",
                "send",
                "--from", "zhiwei@known-blocks1",
                "--to", "lancha@macmini",
                "--title", "Inspect database",
                "--constraint", "Read-only checks only.",
            )
            msg = json.loads(Path(sent.stdout.strip()).read_text(encoding="utf-8"))

            self.assertIn("Read-only checks only.", msg["body"])
            self.assertIn("Do not make destructive changes.", msg["body"])
            self.assertIn("Do not restart services or modify configuration", msg["body"])
            self.assertIn("Do not expose secrets in the reply.", msg["body"])
            self.assertIn("If write/restart/delete/migration is needed", msg["body"])

    def test_task_send_then_receipt_queues_lancha_request_without_body_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            secret = "SECRET_LANCHA_TASK_BODY"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lancha@macmini")
            sent = run_cli(
                base,
                "task",
                "send",
                "--from", "zhiwei@known-blocks1",
                "--to", "lancha@macmini",
                "--title", "Lancha smoke",
                "--context", secret,
                "--capability", "terminal",
            )
            msg = json.loads(Path(sent.stdout.strip()).read_text(encoding="utf-8"))

            receipt = load_json(run_cli(base, "receipt", "--agent", "lancha@macmini", "--allow-from", "zhiwei@known-blocks1", "--once", "--json").stdout)
            queued = load_json(run_cli(base, "queued", "--agent", "lancha@macmini").stdout)
            timeline = load_json(run_cli(base, "timeline", msg["thread_id"]).stdout)

            self.assertEqual(receipt["count"], 1)
            self.assertTrue(receipt["results"][0]["queued_for_human"])
            self.assertEqual(receipt["results"][0]["reason"], "request_requires_human")
            self.assertNotIn(secret, json.dumps(receipt))
            self.assertEqual(queued["count"], 1)
            self.assertEqual(queued["messages"][0]["thread_id"], msg["thread_id"])
            self.assertNotIn(secret, json.dumps(queued))
            self.assertTrue(any(event["event_type"] == "receipt_queued_for_human" for event in timeline["events"]))
            self.assertNotIn(secret, json.dumps(timeline))

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

    def test_self_message_send_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")

            result = run_cli(
                base,
                "send",
                "--from", "zhiwei@known-blocks1",
                "--to", "zhiwei@known-blocks1",
                "--type", "status",
                "--subject", "self loop",
                "--body", "do not create this",
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("self messages are not allowed", result.stderr)
            inbox = base / "inbox" / "zhiwei_known-blocks1"
            self.assertEqual(list(inbox.glob("*.json")), [])

    def test_inbound_self_message_is_rejected_by_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            payload = {
                "version": "a2a.v1",
                "id": "msg_self_inbound",
                "from": "zhiwei@known-blocks1",
                "to": "zhiwei@known-blocks1",
                "type": "status",
                "subject": "loop",
                "body": "do not process",
                "created_at": "2026-05-08T00:00:00Z",
            }
            inbox = base / "inbox" / "zhiwei_known-blocks1"
            (inbox / "self.json").write_text(json.dumps(payload), encoding="utf-8")

            result = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "zhiwei@known-blocks1").stdout)

            self.assertEqual(result["count"], 1)
            self.assertFalse(result["results"][0]["ok"])
            self.assertIn("self messages are not allowed", result["results"][0]["error"])
            self.assertTrue(list((base / "archive" / "failed").glob("*.json")))
            self.assertEqual(list(inbox.glob("*.json")), [])

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

    def test_same_filename_claim_does_not_overwrite_processing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            inbox = base / "inbox" / "zhiwei_known-blocks1"
            processing = base / "processing" / "zhiwei_known-blocks1"
            existing = processing / "same_deadbeef.json"
            existing.write_text("do-not-overwrite", encoding="utf-8")
            payload = {
                "version": "a2a.v1",
                "id": "msg_same",
                "from": "lulu@kamac",
                "to": "zhiwei@known-blocks1",
                "type": "request",
                "subject": "same",
                "body": "hello",
                "created_at": "2026-05-08T00:00:00Z",
            }
            (inbox / "same.json").write_text(json.dumps(payload), encoding="utf-8")
            result = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac").stdout)
            self.assertEqual(result["count"], 1)
            self.assertEqual(existing.read_text(encoding="utf-8"), "do-not-overwrite")

    def test_invalid_sender_goes_to_failed_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            payload = {
                "version": "a2a.v1",
                "id": "msg_mallory_bad",
                "from": "mallory",
                "to": "zhiwei@known-blocks1",
                "type": "request",
                "subject": "bad",
                "body": "run this",
                "created_at": "2026-05-08T00:00:00Z",
            }
            inbox = base / "inbox" / "zhiwei_known-blocks1"
            (inbox / "mallory.json").write_text(json.dumps(payload), encoding="utf-8")
            result = load_json(run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac", "--ack").stdout)
            self.assertEqual(result["count"], 1)
            self.assertFalse(result["results"][0]["ok"])
            self.assertTrue(list((base / "archive" / "failed").glob("*.json")))

    def test_threads_include_operator_fields_and_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            sent = run_cli(
                base,
                "send",
                "--from", "lulu@kamac",
                "--to", "zhiwei@known-blocks1",
                "--type", "request",
                "--subject", "needs triage",
                "--body", "SECRET_THREAD_BODY",
                "--needs-reply",
            )
            msg_path = Path(sent.stdout.strip())
            msg = json.loads(msg_path.read_text(encoding="utf-8"))

            threads = load_json(run_cli(base, "threads", "--needs-reply", "--event-type", "sent").stdout)
            row = next(item for item in threads["threads"] if item["thread_id"] == msg["thread_id"])

            self.assertEqual(row["events"], 1)
            self.assertIsNotNone(row["first_timestamp"])
            self.assertIsNotNone(row["last_timestamp"])
            self.assertEqual(row["last_event"], "sent")
            self.assertEqual(row["participants"], ["lulu@kamac", "zhiwei@known-blocks1"])
            self.assertEqual(row["message_ids"], [msg["id"]])
            self.assertFalse(row["failed"])
            self.assertEqual(row["pending_count"], 1)
            self.assertEqual(row["queued_count"], 0)
            self.assertTrue(row["needs_reply"])
            self.assertNotIn("SECRET_THREAD_BODY", json.dumps(threads))

            archived_only = load_json(run_cli(base, "threads", "--event-type", "archived").stdout)
            self.assertEqual(archived_only["count"], 0)

    def test_threads_failed_filter_uses_failure_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            payload = {
                "version": "a2a.v1",
                "id": "msg_mallory_bad",
                "from": "mallory",
                "to": "zhiwei@known-blocks1",
                "type": "request",
                "subject": "bad",
                "body": "SECRET_FAILED_BODY",
                "created_at": "2026-05-08T00:00:00Z",
                "thread_id": "thread_failed",
            }
            inbox = base / "inbox" / "zhiwei_known-blocks1"
            (inbox / "mallory.json").write_text(json.dumps(payload), encoding="utf-8")
            run_cli(base, "poll", "--agent", "zhiwei@known-blocks1", "--allow-from", "lulu@kamac")

            threads = load_json(run_cli(base, "threads", "--failed").stdout)

            self.assertEqual(threads["count"], 1)
            self.assertEqual(threads["threads"][0]["thread_id"], "thread_failed")
            self.assertTrue(threads["threads"][0]["failed"])
            self.assertNotIn("SECRET_FAILED_BODY", json.dumps(threads))

    def test_timeline_json_and_markdown_do_not_echo_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            sent = run_cli(
                base,
                "send",
                "--from", "lulu@kamac",
                "--to", "zhiwei@known-blocks1",
                "--type", "request",
                "--subject", "timeline",
                "--body", "SECRET_TIMELINE_BODY",
            )
            msg = json.loads(Path(sent.stdout.strip()).read_text(encoding="utf-8"))

            timeline = load_json(run_cli(base, "timeline", msg["thread_id"]).stdout)
            markdown = run_cli(base, "timeline", msg["thread_id"], "--markdown").stdout

            self.assertEqual(timeline["count"], 1)
            self.assertEqual(set(timeline["events"][0]), {"timestamp", "event_type", "actor", "message_id", "from", "to"})
            self.assertNotIn("body", json.dumps(timeline))
            self.assertNotIn("SECRET_TIMELINE_BODY", json.dumps(timeline))
            self.assertIn("# Timeline", markdown)
            self.assertIn("- ", markdown)
            self.assertNotIn("SECRET_TIMELINE_BODY", markdown)

    def test_doctor_reports_malformed_processing_without_failure_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            bad = base / "processing" / "lulu_kamac" / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")

            result = run_cli(base, "doctor")
            report = load_json(result.stdout)

            self.assertEqual(result.returncode, 0)
            self.assertTrue(report["ok"])
            self.assertEqual(report["contacts_count"], 2)
            self.assertEqual(report["malformed_json_count"], 1)
            self.assertEqual(report["malformed_json_counts"]["processing"], 1)
            self.assertEqual(report["processing_counts"]["lulu_kamac"], 1)

    def test_threads_tolerate_legacy_naive_event_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            events = base / "events" / "2026-05-08.jsonl"
            events.write_text(json.dumps({
                "event_id": "evt_legacy",
                "event_type": "sent",
                "timestamp": "2026-05-08T00:00:00",
                "actor": "lulu@kamac",
                "message_id": "msg_legacy",
                "thread_id": "thread_legacy",
                "from": "lulu@kamac",
                "to": "zhiwei@known-blocks1",
            }) + "\n", encoding="utf-8")

            result = run_cli(base, "threads", "--days", "9999")
            output = load_json(result.stdout)

            self.assertEqual(output["count"], 1)
            self.assertEqual(output["threads"][0]["thread_id"], "thread_legacy")

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
