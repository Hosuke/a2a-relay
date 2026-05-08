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
init_mailbox(base, ["lulu@kamac", "zhiwei@known-blocks1"])

request = make_message(
    "lulu@kamac",
    "zhiwei@known-blocks1",
    "request",
    "hello",
    "Please review this handoff.",
    needs_reply=True,
)
send_message(base, request)

for inbox_path in list_messages(base, "zhiwei@known-blocks1"):
    claimed = claim_message(base, "zhiwei@known-blocks1", inbox_path)
    if claimed is None:
        continue
    msg = read_message(claimed)
    validate_message(msg, config=RelayConfig(allow_from={"lulu@kamac"}))

    reply = make_message(
        "zhiwei@known-blocks1",
        msg["from"],
        "reply",
        f"Re: {msg['subject']}",
        "Received.",
        reply_to=msg["id"],
        thread_id=msg.get("thread_id"),
    )
    send_message(base, reply)
    mark_seen(base, msg, actor="zhiwei@known-blocks1")
    archive_message(base, claimed, ok=True, message_id=msg["id"])
```

## CLI-Compatible Agent Loop

If an agent is easier to operate through subprocesses, use the CLI:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent zhiwei@known-blocks1

python -m a2a_relay --base /root/agent-mailbox poll \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --ack

python -m a2a_relay --base /root/agent-mailbox reply \
  --from zhiwei@known-blocks1 \
  --to lulu@kamac \
  --reply-to msg_... \
  --thread-id thread_... \
  --body "Received."
```

For operator queues, use:

```bash
python -m a2a_relay --base /root/agent-mailbox queued \
  --agent zhiwei@known-blocks1

python -m a2a_relay --base /root/agent-mailbox pending \
  --agent zhiwei@known-blocks1 \
  --include-processing
```

These commands intentionally show metadata only and do not echo message bodies.

## Dispatcher Integration

The dispatcher is for policy-gated local auto-replies. Configure
`dispatcher.json`, then run one-shot dispatch:

```bash
python -m a2a_relay --base /root/agent-mailbox dispatch \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --action auto-reply \
  --ack
```

Or run continuously:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --dispatch-action auto-reply \
  --interval 10 \
  --ack
```

The configured action receives stdin shaped like:

```text
<a2a_message>
sender: lulu@kamac
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
sudo systemctl enable --now 'a2a-watch@zhiwei@known-blocks1.service'
```

Override `A2A_BASE`, `A2A_ALLOW_FROM`, `WorkingDirectory`, and hardening options
for your deployment. If the agent ID contains characters that are awkward in a
unit instance name, create a wrapper unit or a drop-in with an explicit
`ExecStart`.

## End-To-End Scenario

1. Operator initializes a mailbox with `lulu@kamac` and `zhiwei@known-blocks1`.
2. Operator adds any extra private contacts and aliases.
3. Lulu sends a `request` with `needs_reply=true`.
4. Zhiwei's watcher validates sender policy with `--allow-from lulu@kamac`.
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
