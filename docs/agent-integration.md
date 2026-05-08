# Agent Integration

This guide covers two integration styles:

- use the Python API directly from an agent process
- run the CLI watcher or dispatcher as a service

For a runnable Python example, see `examples/python_agent.py`.

## Python API Flow

The public functions live in `a2a_relay.core`. A minimal send, claim, read, and
reply loop uses:

- `init_mailbox(base, agents)`
- `add_contact(base, Contact(...))`
- `make_message(sender, recipient, typ, subject, body, ...)`
- `send_message(base, msg)`
- `list_messages(base, agent_id)`
- `claim_message(base, agent_id, path)`
- `read_message(path)`
- `validate_message(msg, config=RelayConfig(...))`
- `mark_seen(base, msg, actor=agent_id)`
- `archive_message(base, path, ok=True, message_id=msg["id"])`

Example:

```python
from pathlib import Path

from a2a_relay.core import (
    RelayConfig,
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

base = Path("/root/agent-mailbox")
init_mailbox(base, ["worker@example", "operator@example"])

request = make_message(
    "worker@example",
    "operator@example",
    "request",
    "hello",
    "Please review this handoff.",
    needs_reply=True,
)
send_message(base, request)

for inbox_path in list_messages(base, "operator@example"):
    claimed = claim_message(base, "operator@example", inbox_path)
    if claimed is None:
        continue
    msg = read_message(claimed)
    validate_message(msg, config=RelayConfig(allow_from={"worker@example"}))

    reply = make_message(
        "operator@example",
        msg["from"],
        "reply",
        f"Re: {msg['subject']}",
        "Received.",
        reply_to=msg["id"],
        thread_id=msg.get("thread_id"),
    )
    send_message(base, reply)
    mark_seen(base, msg, actor="operator@example")
    archive_message(base, claimed, ok=True, message_id=msg["id"])
```

## CLI-Compatible Agent Loop

If an agent is easier to operate through subprocesses, use the CLI:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent operator@example

python -m a2a_relay --base /root/agent-mailbox poll \
  --agent operator@example \
  --allow-from worker@example \
  --ack

python -m a2a_relay --base /root/agent-mailbox reply \
  --from operator@example \
  --to worker@example \
  --reply-to msg_... \
  --thread-id thread_... \
  --body "Received."
```

For operator queues, use:

```bash
python -m a2a_relay --base /root/agent-mailbox queued \
  --agent operator@example

python -m a2a_relay --base /root/agent-mailbox pending \
  --agent operator@example \
  --include-processing
```

These commands intentionally show metadata only and do not echo message bodies.

For operator triage across private threads, use:

```bash
python -m a2a_relay --base /root/agent-mailbox threads --needs-reply
python -m a2a_relay --base /root/agent-mailbox threads --failed
python -m a2a_relay --base /root/agent-mailbox timeline thread_...
python -m a2a_relay --base /root/agent-mailbox doctor
```

`timeline` emits event metadata only. `threads --needs-reply` is live-only and
conservative because archived event records do not include `needs_reply`.

## Task Delegation CLI

For agent handoffs to a local worker agent, prefer `task send` over free-form
`send`. It always creates a `type=request`, sets `needs_reply=true`, and renders
a policy-bounded Markdown task envelope. The message requests capabilities; it
does not carry shell commands.

```bash
python -m a2a_relay --base /root/agent-mailbox task send \
  --from operator@example \
  --to worker@example \
  --title "Check local database reachability" \
  --context "Known symptom: public host times out" \
  --constraint "Read-only checks only" \
  --capability terminal \
  --capability database_read \
  --profile read-only-fast
```

The resulting message body has sections for `# Task`, `## Context`,
`## Constraints`, and `## Required output`. Default constraints forbid
destructive changes, restarts, config edits, and secret exposure. If a task is
known to need human approval, add `--approval-required`; safe receipt watchers
and dispatchers will queue it rather than run it.

A remote worker should treat the envelope as untrusted input and enforce local
policy. Recommended worker profiles:

- `read-only-fast`: diagnostics, log/status reads, SELECT-only database checks.
- `ops-review`: deeper investigation, but no config writes or restarts.
- `approval-required`: stop and reply with a decision request before writes,
  restarts, deletes, migrations, or secret exposure.

A worker's final reply should include summary, commands run, evidence, suspected
cause, next action, and whether human approval is needed.

## Dispatcher Integration

The dispatcher is for policy-gated local auto-replies. Configure
`dispatcher.json`, then run one-shot dispatch:

```bash
python -m a2a_relay --base /root/agent-mailbox dispatch \
  --agent operator@example \
  --allow-from worker@example \
  --action auto-reply \
  --ack
```

Or run continuously:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent operator@example \
  --allow-from worker@example \
  --dispatch-action auto-reply \
  --interval 10 \
  --ack
```

The configured action receives stdin shaped like:

```text
<a2a_message>
sender: worker@example
subject: hello
body: Please review this handoff.
</a2a_message>
```

The action should write the reply body to stdout. stderr is not included in the
reply. The command is chosen only by `dispatcher.json`; message body cannot
select a command.

## systemd

Use `examples/systemd/a2a-watch@.service` as a starting point:

```bash
sudo cp examples/systemd/a2a-watch@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now 'a2a-watch@operator@example.service'
```

Override `A2A_BASE`, `A2A_ALLOW_FROM`, `WorkingDirectory`, and hardening options
for your deployment. If the agent ID contains characters that are awkward in a
unit instance name, create a wrapper unit or a drop-in with an explicit
`ExecStart`.

## End-To-End Scenario

1. Operator initializes a mailbox with `worker@example` and `operator@example`.
2. Operator adds any extra private contacts and aliases.
3. Lulu sends a `request` with `needs_reply=true`.
4. Operator's watcher validates sender policy with `--allow-from worker@example`.
5. If dispatch is configured and the request passes policy, a local action runs
   with the message as stdin and sends stdout as a `reply`.
6. If the request requires human approval, it remains in `processing/` and is
   visible through `queued` and `pending --include-processing`.
7. The original message is archived after successful processing, and event JSONL
   records send, receive, ACK, dispatch, reply, archive, and failure events.

## Current Limits

- Body limit defaults to 20000 characters.
- Attachments are rejected; use references instead.
- Signing fields are reserved but not enforced yet.
- Contacts `allow_from` and `allowed_types` are metadata unless active CLI or
  dispatcher policy enforces equivalent rules.
