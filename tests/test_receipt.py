from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "a2a_relay"]

sys.path.insert(0, str(ROOT))

from a2a_relay.core import (
    Contact,
    add_contact,
    claim_message,
    inbox_dir,
    init_mailbox,
    make_message,
    processing_dir,
    send_message,
)


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


class ReceiptCLITest(unittest.TestCase):
    def _setup_mailbox(self, tmp: str) -> Path:
        base = Path(tmp) / "mailbox"
        init_mailbox(base, ["lulu@kamac"])
        contacts_path = base / "contacts.json"
        contacts = json.loads(contacts_path.read_text(encoding="utf-8"))
        contacts["contacts"]["lulu@kamac"]["aliases"] = ["lulu"]
        contacts_path.write_text(json.dumps(contacts), encoding="utf-8")
        add_contact(base, Contact(id="zhiwei@known-blocks1", aliases=["possum"]))
        return base

    def _receipt_once(self, base: Path, *extra: str) -> dict:
        result = run_cli(
            base,
            "receipt",
            "--agent", "lulu@kamac",
            "--allow-from", "possum",
            "--once",
            "--json",
            *extra,
        )
        return load_json(result.stdout)

    def test_note_is_logged_and_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            msg = make_message("zhiwei@known-blocks1", "lulu@kamac", "note", "hello", "SECRET_NOTE_BODY")
            sent_path = send_message(base, msg)

            output = self._receipt_once(base)

            self.assertEqual(output["count"], 1)
            self.assertTrue(output["results"][0]["ok"])
            self.assertIn("archive", output["results"][0])
            self.assertFalse(sent_path.exists())
            self.assertEqual(list(inbox_dir(base, "lulu@kamac").glob("*.json")), [])
            self.assertTrue(list((base / "archive" / "processed").glob("*.json")))
            events = "\n".join(p.read_text(encoding="utf-8") for p in (base / "events").glob("*.jsonl"))
            self.assertIn('"event_type": "received"', events)
            self.assertIn('"event_type": "receipt_logged"', events)

    def test_reply_is_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            send_message(base, make_message("zhiwei@known-blocks1", "lulu@kamac", "reply", "Re", "ok"))

            output = self._receipt_once(base)

            self.assertTrue(output["results"][0]["ok"])
            self.assertEqual(output["results"][0]["type"], "reply")
            self.assertIn("archive", output["results"][0])

    def test_request_is_queued_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            send_message(base, make_message("zhiwei@known-blocks1", "lulu@kamac", "request", "please", "do work"))

            output = self._receipt_once(base)

            self.assertTrue(output["results"][0]["queued_for_human"])
            self.assertEqual(output["results"][0]["reason"], "request_requires_human")
            self.assertNotIn("archive", output["results"][0])
            processing_files = list((base / "processing" / "lulu_kamac").glob("*.json"))
            self.assertEqual(len(processing_files), 1)

    def test_human_approval_is_queued_even_with_archive_requests_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            msg = make_message("zhiwei@known-blocks1", "lulu@kamac", "request", "sensitive", "needs approval")
            data = msg.to_json_dict()
            data["human_approval_required"] = True
            path = inbox_dir(base, "lulu@kamac") / f"{data['id']}.json"
            path.write_text(json.dumps(data), encoding="utf-8")

            output = self._receipt_once(base, "--archive-requests-too")

            self.assertTrue(output["results"][0]["queued_for_human"])
            self.assertEqual(output["results"][0]["reason"], "human_approval_required")
            self.assertNotIn("archive", output["results"][0])
            self.assertEqual(len(list((base / "processing" / "lulu_kamac").glob("*.json"))), 1)

    def test_unknown_sender_is_rejected_to_failed_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            payload = {
                "version": "a2a.v1",
                "id": "msg_unknown_sender",
                "from": "mallory@evil",
                "to": "lulu@kamac",
                "type": "note",
                "subject": "bad",
                "body": "ignore me",
                "created_at": "2026-05-09T00:00:00Z",
            }
            (inbox_dir(base, "lulu@kamac") / "mallory.json").write_text(json.dumps(payload), encoding="utf-8")

            output = self._receipt_once(base)

            self.assertFalse(output["results"][0]["ok"])
            self.assertIn("sender not allowed", output["results"][0]["error"])
            self.assertTrue(list((base / "archive" / "failed").glob("*.json")))

    def test_body_is_not_echoed_in_cli_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            send_message(base, make_message("zhiwei@known-blocks1", "lulu@kamac", "note", "body test", "SECRET_BODY_MARKER"))

            result = run_cli(
                base,
                "receipt",
                "--agent", "lulu@kamac",
                "--allow-from", "possum",
                "--once",
                "--json",
            )

            self.assertNotIn("SECRET_BODY_MARKER", result.stdout)
            output = load_json(result.stdout)
            self.assertNotIn("message", output["results"][0])

    def test_status_does_not_create_recursive_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            send_message(base, make_message("zhiwei@known-blocks1", "lulu@kamac", "status", "ACK", "received"))

            output = self._receipt_once(base)

            self.assertTrue(output["results"][0]["ok"])
            self.assertEqual(output["results"][0]["type"], "status")
            self.assertEqual(list(inbox_dir(base, "zhiwei@known-blocks1").glob("*.json")), [])

    def test_pending_without_include_processing_is_inbox_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            send_message(base, make_message("zhiwei@known-blocks1", "lulu@kamac", "note", "inbox note", "INBOX_BODY"))
            request_path = send_message(
                base,
                make_message("zhiwei@known-blocks1", "lulu@kamac", "request", "queued request", "SECRET_QUEUED_BODY"),
            )
            self.assertIsNotNone(claim_message(base, "lulu@kamac", request_path))

            result = run_cli(base, "pending", "--agent", "lulu")
            output = load_json(result.stdout)

            self.assertEqual(output["count"], 1)
            self.assertEqual(output["messages"][0]["subject"], "inbox note")
            self.assertNotIn("queued request", result.stdout)
            self.assertNotIn("SECRET_QUEUED_BODY", result.stdout)

    def test_pending_include_processing_shows_queued_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            request_path = send_message(
                base,
                make_message("zhiwei@known-blocks1", "lulu@kamac", "request", "queued request", "SECRET_QUEUED_BODY"),
            )
            claimed = claim_message(base, "lulu@kamac", request_path)
            self.assertIsNotNone(claimed)

            result = run_cli(base, "pending", "--agent", "lulu", "--include-processing")
            output = load_json(result.stdout)

            self.assertEqual(output["count"], 1)
            self.assertEqual(output["messages"][0]["type"], "request")
            self.assertEqual(output["messages"][0]["subject"], "queued request")
            self.assertEqual(output["messages"][0]["needs_reply"], False)
            self.assertEqual(output["messages"][0]["human_approval_required"], False)
            self.assertNotIn("SECRET_QUEUED_BODY", result.stdout)

    def test_queued_command_shows_processing_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            request_path = send_message(
                base,
                make_message("zhiwei@known-blocks1", "lulu@kamac", "request", "queued command", "SECRET_QUEUED_BODY"),
            )
            claimed = claim_message(base, "lulu@kamac", request_path)
            self.assertIsNotNone(claimed)

            result = run_cli(base, "queued", "--agent", "lulu")
            output = load_json(result.stdout)

            self.assertEqual(output["count"], 1)
            self.assertEqual(output["messages"][0]["path"], str(claimed))
            self.assertEqual(output["messages"][0]["subject"], "queued command")
            self.assertNotIn("SECRET_QUEUED_BODY", result.stdout)

    def test_malformed_processing_is_visible_as_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            bad_path = processing_dir(base, "lulu@kamac") / "bad.json"
            bad_path.write_text("{not valid json", encoding="utf-8")

            result = run_cli(base, "queued", "--agent", "lulu")
            output = load_json(result.stdout)

            self.assertEqual(output["count"], 1)
            self.assertFalse(output["messages"][0]["ok"])
            self.assertEqual(output["messages"][0]["path"], str(bad_path))
            self.assertIn("JSONDecodeError", output["messages"][0]["error"])

    def test_recover_processing_can_finish_archivable_message_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            msg = make_message("zhiwei@known-blocks1", "lulu@kamac", "note", "restart", "body")
            inbox_path = send_message(base, msg)
            claimed = claim_message(base, "lulu@kamac", inbox_path)
            self.assertIsNotNone(claimed)
            self.assertTrue(claimed.exists())

            output = self._receipt_once(base, "--recover-processing")

            self.assertEqual(output["count"], 1)
            self.assertTrue(output["results"][0]["ok"])
            self.assertIn("archive", output["results"][0])
            self.assertFalse(claimed.exists())

    def test_recover_processing_reports_queued_request_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._setup_mailbox(tmp)
            msg = make_message("zhiwei@known-blocks1", "lulu@kamac", "request", "restart request", "body")
            inbox_path = send_message(base, msg)
            claimed = claim_message(base, "lulu@kamac", inbox_path)
            self.assertIsNotNone(claimed)

            output = self._receipt_once(base, "--recover-processing")

            self.assertEqual(output["count"], 1)
            self.assertTrue(output["results"][0]["queued_for_human"])
            self.assertTrue(claimed.exists())


if __name__ == "__main__":
    unittest.main()
