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


class A2ARelayContactsCLITest(unittest.TestCase):
    def test_init_creates_contacts_and_alias_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac")
            run_cli(
                base,
                "contacts", "add",
                "--id", "kames@kamac",
                "--display-name", "kames",
                "--alias", "kames",
                "--alias", "kam",
                "--notes", "private contact on kamac",
            )
            contacts = load_json(run_cli(base, "contacts", "list").stdout)
            self.assertEqual(contacts["count"], 3)
            self.assertTrue(any(c["id"] == "kames@kamac" and "kames" in c["aliases"] for c in contacts["contacts"]))

            shown = load_json(run_cli(base, "contacts", "show", "kam").stdout)
            self.assertEqual(shown["id"], "kames@kamac")

            sent = run_cli(base, "send", "--from", "lulu@kamac", "--to", "kames", "--subject", "hi", "--body", "hello")
            path = Path(sent.stdout.strip())
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["to"], "kames@kamac")

    def test_alias_conflict_and_unknown_alias_fail_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1")
            run_cli(base, "contacts", "add", "--id", "lulu@kamac", "--alias", "lulu")
            duplicate = run_cli(base, "contacts", "add", "--id", "kames@kamac", "--alias", "lulu", check=False)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("alias already used", duplicate.stderr)

            unknown = run_cli(base, "contacts", "show", "missing", check=False)
            self.assertNotEqual(unknown.returncode, 0)
            self.assertIn("unknown contact", unknown.stderr)

    def test_remove_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1")
            run_cli(base, "contacts", "add", "--id", "lulu@kamac", "--alias", "lulu")
            removed = load_json(run_cli(base, "contacts", "remove", "lulu").stdout)
            self.assertEqual(removed["removed"], "lulu@kamac")
            contacts = load_json(run_cli(base, "contacts", "list").stdout)
            self.assertFalse(any(c["id"] == "lulu@kamac" for c in contacts["contacts"]))

    def test_ambiguous_alias_fails_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "a@x", "--agent", "b@x")
            contacts_file = base / "contacts.json"
            data = json.loads(contacts_file.read_text(encoding="utf-8"))
            data["contacts"]["a@x"]["aliases"] = ["shared"]
            data["contacts"]["b@x"]["aliases"] = ["shared"]
            contacts_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            result = run_cli(base, "send", "--from", "a@x", "--to", "shared",
                             "--subject", "test", "--body", "test", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous", result.stderr)

    def test_unknown_typo_fails_when_contacts_json_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "alice@x")
            result = run_cli(base, "send", "--from", "alice@x", "--to", "alce@x",
                             "--subject", "test", "--body", "test", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown contact", result.stderr)

    def test_existing_agent_not_in_contacts_fails_when_contacts_json_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "alice@x")
            contacts_file = base / "contacts.json"
            data = json.loads(contacts_file.read_text(encoding="utf-8"))
            del data["contacts"]["alice@x"]
            contacts_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            result = run_cli(base, "send", "--from", "alice@x", "--to", "alice@x",
                             "--subject", "test", "--body", "test", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown contact", result.stderr)

    def test_display_name_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "alice@x")
            run_cli(base, "contacts", "add", "--id", "bob@x", "--display-name", "Bob")
            result = run_cli(base, "contacts", "add", "--id", "charlie@x",
                             "--display-name", "Bob", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("display_name conflicts", result.stderr)

    def test_id_conflict_with_existing_display_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "alice@x")
            run_cli(base, "contacts", "add", "--id", "bob@x", "--display-name", "Bob")
            result = run_cli(base, "contacts", "add", "--id", "Bob", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("conflicts", result.stderr)

    def test_no_accidental_inbox_or_event_on_failed_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "alice@x")
            result = run_cli(base, "send", "--from", "alice@x", "--to", "typo@x",
                             "--subject", "test", "--body", "test", check=False)
            self.assertNotEqual(result.returncode, 0)
            inbox_files = list((base / "inbox").rglob("*.json"))
            self.assertEqual(len(inbox_files), 0)
            event_files = list((base / "events").rglob("*.jsonl"))
            self.assertEqual(len(event_files), 0)

    def test_threads_can_filter_by_contact_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mailbox"
            run_cli(base, "init", "--agent", "zhiwei@known-blocks1", "--agent", "lulu@kamac", "--agent", "kames@kamac")
            run_cli(base, "contacts", "add", "--id", "observer@known-blocks1", "--alias", "obs")
            run_cli(base, "send", "--from", "lulu@kamac", "--to", "zhiwei@known-blocks1", "--subject", "lulu", "--body", "hello")
            run_cli(base, "send", "--from", "observer@known-blocks1", "--to", "zhiwei@known-blocks1", "--subject", "obs", "--body", "hello")
            all_threads = load_json(run_cli(base, "threads").stdout)
            filtered = load_json(run_cli(base, "threads", "--contact", "obs").stdout)
            self.assertGreaterEqual(all_threads["count"], 2)
            self.assertEqual(filtered["count"], 1)


if __name__ == "__main__":
    unittest.main()
