#!/usr/bin/env python3
"""Minimal stdlib/Python API integration for A2A Relay.

Run from the repository root:

    python examples/python_agent.py

The example creates a temporary mailbox, sends a request, claims and reads it as
the receiver, replies on the same thread, then claims the reply as the sender.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Allow running this example directly from a source checkout without installing
# the package first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a2a_relay.core import (
    Contact,
    RelayConfig,
    add_contact,
    archive_message,
    claim_message,
    init_mailbox,
    list_messages,
    make_message,
    mark_seen,
    read_message,
    send_message,
    validate_message,
)


def handle_one_message(base: Path, agent_id: str, *, allow_from: set[str]) -> dict | None:
    """Claim one inbox message, validate it, reply, and archive the original."""
    config = RelayConfig(allow_from=allow_from)

    for inbox_path in list_messages(base, agent_id):
        claimed = claim_message(base, agent_id, inbox_path)
        if claimed is None:
            continue

        msg = read_message(claimed)
        validate_message(msg, config=config)

        reply = make_message(
            agent_id,
            msg["from"],
            "reply",
            f"Re: {msg['subject']}",
            "Received via Python API.",
            reply_to=msg["id"],
            thread_id=msg.get("thread_id"),
        )
        reply_path = send_message(base, reply)

        mark_seen(base, msg, actor=agent_id)
        archive_message(base, claimed, ok=True, message_id=msg["id"])

        return {
            "received": msg["id"],
            "reply_path": str(reply_path),
            "thread_id": msg.get("thread_id"),
        }

    return None


def claim_reply(base: Path, agent_id: str, *, allow_from: set[str]) -> dict | None:
    """Claim one reply/status style message and archive it."""
    config = RelayConfig(allow_from=allow_from)

    for inbox_path in list_messages(base, agent_id):
        claimed = claim_message(base, agent_id, inbox_path)
        if claimed is None:
            continue

        msg = read_message(claimed)
        validate_message(msg, config=config)
        mark_seen(base, msg, actor=agent_id)
        archive_message(base, claimed, ok=True, message_id=msg["id"])
        return msg

    return None


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "agent-mailbox"

        init_mailbox(base, ["worker@example", "operator@example"])
        add_contact(base, Contact(id="operator@localhost", aliases=["operator"]))

        request = make_message(
            "worker@example",
            "operator@example",
            "request",
            "python api smoke test",
            "Please confirm you can read and reply.",
            needs_reply=True,
            idempotency_key="python-agent-example-001",
        )
        request_path = send_message(base, request)

        handled = handle_one_message(
            base,
            "operator@example",
            allow_from={"worker@example"},
        )
        reply = claim_reply(base, "worker@example", allow_from={"operator@example"})

        print(json.dumps({
            "mailbox": str(base),
            "request_path": str(request_path),
            "handled": handled,
            "reply": {
                "id": reply["id"] if reply else None,
                "type": reply["type"] if reply else None,
                "subject": reply["subject"] if reply else None,
                "body": reply["body"] if reply else None,
            },
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
