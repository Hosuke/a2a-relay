# Getting Started

This guide walks through the smallest useful A2A Relay flow:

1. initialize a filesystem mailbox
2. send a request
3. inspect pending work
4. poll and acknowledge the request
5. reply on the same thread
6. understand the mailbox layout

The examples use `/root/agent-mailbox` as the shared mailbox root.

## Initialize A Mailbox

Create a mailbox with two agents:

```bash
python -m a2a_relay --base /root/agent-mailbox init \
  --agent operator@example \
  --agent worker@example
```

`init` creates the base directories, `agents.json`, and `contacts.json`. Agent
IDs are mapped to filesystem-safe directory names by replacing characters outside
`A-Za-z0-9_.-` with `_`.

## Send A Request

Send a request from `worker@example` to `operator@example`:

```bash
python -m a2a_relay --base /root/agent-mailbox send \
  --from worker@example \
  --to operator@example \
  --type request \
  --subject "hello" \
  --body "Please check this handoff." \
  --needs-reply
```

The command prints the path of the JSON file written into the recipient inbox.

## Inspect Pending Work

Show messages waiting in an agent inbox:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent operator@example
```

For operator triage, include messages already claimed into `processing/`:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent operator@example \
  --include-processing
```

List only queued or claimed processing messages:

```bash
python -m a2a_relay --base /root/agent-mailbox queued \
  --agent operator@example
```

`pending`, `pending --include-processing`, and `queued` print metadata only:
path, id, from, to, type, subject, thread_id, needs_reply, and
human_approval_required. They do not print message bodies.

## Inspect Threads And Health

List recent private threads from the event log:

```bash
python -m a2a_relay --base /root/agent-mailbox threads
```

Useful operator filters:

```bash
python -m a2a_relay --base /root/agent-mailbox threads --contact worker
python -m a2a_relay --base /root/agent-mailbox threads --failed
python -m a2a_relay --base /root/agent-mailbox threads --needs-reply
python -m a2a_relay --base /root/agent-mailbox threads --event-type sent
```

`--needs-reply` is conservative: the event log does not store `needs_reply`, so
the command infers it only from current `inbox/` and `processing/` messages with
`needs_reply=true` or `type=request`. `--failed` is a thread-level predicate
computed from all events in the selected time window, even if `--event-type`
filters the row's counted events.

For a single thread, show metadata-only events as JSON or Markdown:

```bash
python -m a2a_relay --base /root/agent-mailbox timeline thread_worker_operator_hello
python -m a2a_relay --base /root/agent-mailbox timeline thread_worker_operator_hello --markdown
```

For a quick mailbox health check:

```bash
python -m a2a_relay --base /root/agent-mailbox doctor
```

`doctor` reports expected directory presence, contact count, per-agent inbox and
processing counts, failed archives, event files, and malformed JSON in live
message directories.

## Poll And ACK

Poll claims messages atomically into `processing/<agent>/`, validates them, logs
receipt events, optionally sends a status ACK, and archives successfully handled
messages:

```bash
python -m a2a_relay --base /root/agent-mailbox poll \
  --agent operator@example \
  --allow-from worker@example \
  --ack
```

The validator default and current CLI maximum body setting is 20000 characters.
Use `--max-body-chars` to make a watcher stricter.

## Reply

A reply should preserve the original message id and thread id. You can copy them
from `pending` output before polling, from the claimed message file, or from the
event log:

```bash
python -m a2a_relay --base /root/agent-mailbox reply \
  --from operator@example \
  --to worker@example \
  --reply-to msg_20260508_163000_worker_to_operator \
  --thread-id thread_worker_operator_hello \
  --body "Received. I will take a look."
```

If you have the original message file, `reply` can infer the recipient,
`reply_to`, thread, and default subject:

```bash
python -m a2a_relay --base /root/agent-mailbox reply \
  --from operator@example \
  --message-file /root/agent-mailbox/processing/operator_example/request.json \
  --body "Received. I will take a look."
```

## Watch Continuously

For a long-running watcher:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent operator@example \
  --allow-from worker@example \
  --interval 10 \
  --ack
```

See `examples/systemd/a2a-watch@.service` for a systemd template.

## Safe Receipt Watcher

Use `receipt` when an agent should safely log low-risk receipts without running
dispatch:

```bash
python -m a2a_relay --base /root/agent-mailbox receipt \
  --agent worker@example \
  --allow-from operator@example \
  --once \
  --json
```

By default, `receipt` archives `note`, `status`, `reply`, and `heartbeat`.
`request` messages stay in `processing/<agent>/` for human handling unless
`--archive-requests-too` is used. Messages with `human_approval_required=true`
are always queued. After a restart, use `--recover-processing` to re-report or
finish messages already claimed before the crash.

## Mailbox Layout

```text
agent-mailbox/
├── agents.json
├── contacts.json
├── dispatcher.json          # optional
├── inbox/
│   ├── operator_example/
│   └── worker_example/
├── processing/
│   ├── operator_example/
│   └── worker_example/
├── archive/
│   ├── processed/
│   └── failed/
├── events/
│   └── YYYY-MM-DD.jsonl
├── state/
│   └── seen.jsonl
├── logs/
├── locks/
└── tmp/
```

`inbox/` holds unclaimed messages. `processing/` holds messages currently being
handled or queued for an operator. `archive/processed/` and `archive/failed/`
hold completed and rejected messages. `events/` is append-only JSONL audit data.
`state/seen.jsonl` records message IDs and idempotency keys to avoid duplicate
processing.

## More Detail

- Message fields and validation: `docs/message-schema.md`
- Contacts and dispatcher configuration: `docs/configuration.md`
- Python and service integration: `docs/agent-integration.md`
- Adding another private agent: `docs/adding-an-agent.md`
