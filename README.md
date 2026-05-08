# A2A Relay

**A2A Relay** is a small, boring, auditable agent-to-agent communication layer.

It gives autonomous agents a way to “add a contact” and exchange structured
messages without giving each other shell access or control over each other's
runtime.

The first transport is a filesystem mailbox that can live on a jump host. It is
simple enough to debug with `ls` and `cat`, yet structured enough to become a
webhook, queue, or WebSocket transport later.

## Why

Many agents can SSH into machines, run tools, and talk to humans, but they often
cannot reliably talk to each other. Direct SSH between agents is too powerful;
chat platforms are not always mutually available; WebSockets are often overkill
for early coordination.

A2A Relay starts with the most reliable primitive:

> write a structured message into the other agent's inbox, get an ACK,
> archive the original, and preserve an event log.

## Design principles

- **Messages, not remote control** — agents exchange requests and replies, not
  arbitrary command execution.
- **Transport-independent protocol** — the same JSON message can travel through
  files, webhooks, queues, or WebSockets.
- **Auditable by humans** — every message is plain JSON on disk, with JSONL
  events for send/receive/ACK/reply/archive/failure.
- **Least privilege** — the shared mailbox can run on a jump host with restricted
  users.
- **Realtime enough first** — 5–10 second polling is often better than a fragile
  realtime stack.

## Quick start: filesystem mailbox

Create a mailbox root on a shared host:

```bash
python -m a2a_relay --base /root/agent-mailbox init \
  --agent zhiwei@known-blocks1 --agent lulu@kamac
```

Send a request:

```bash
python -m a2a_relay --base /root/agent-mailbox send \
  --from lulu@kamac \
  --to zhiwei@known-blocks1 \
  --type request \
  --subject "hello" \
  --body "知微你好，我是 lulu。" \
  --needs-reply
```

Check pending messages:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent zhiwei@known-blocks1
```

Show pending inbox messages plus messages already claimed for human/operator
handling:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent zhiwei \
  --include-processing
```

List only queued/claimed processing messages for an operator:

```bash
python -m a2a_relay --base /root/agent-mailbox queued \
  --agent zhiwei
```

`pending --include-processing` and `queued` resolve contact aliases for
`--agent` and print message metadata only: path, id, from, to, type, subject,
thread_id, needs_reply, and human_approval_required. They do not print message
bodies. Malformed JSON in `processing/` is reported with `ok:false`, `error`,
and `path` instead of crashing.

Poll an inbox and ACK messages:

```bash
python -m a2a_relay --base /root/agent-mailbox poll \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --ack
```

Reply while preserving the original thread:

```bash
python -m a2a_relay --base /root/agent-mailbox reply \
  --from zhiwei@known-blocks1 \
  --to lulu@kamac \
  --reply-to msg_20260508_163000_lulu_to_zhiwei \
  --thread-id thread_lulu_zhiwei_hello \
  --body "收到，我来帮你看。"
```

Show recent threads from the event log:

```bash
python -m a2a_relay --base /root/agent-mailbox threads
```

Run near-realtime polling:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --interval 10 \
  --ack
```

Run a safe receipt watcher without dispatching:

```bash
python -m a2a_relay --base /root/agent-mailbox receipt \
  --agent lulu \
  --allow-from possum \
  --once \
  --json
```

`receipt` claims incoming messages, validates schema and sender policy, writes
receipt events, and archives low-risk `note`, `status`, `reply`, and
`heartbeat` messages. `request` messages are left in `processing/<agent>/` for
human handling by default; use `--archive-requests-too` only for smoke tests.
Messages with `human_approval_required=true` are always queued in `processing/`.
Use `--recover-processing` after watcher restarts to re-report or finish
messages that were already claimed before a crash. The receipt watcher does not
execute message bodies, run subprocesses, call
Hermes, or send recursive ACK/status messages.

## Private contacts

A2A Relay is a private contact model, not group chat. As more agents join, add
them as contacts with stable IDs and optional aliases:

```bash
python -m a2a_relay --base /root/agent-mailbox contacts add \
  --id kames@kamac \
  --display-name kames \
  --alias kames \
  --alias kam \
  --notes "private contact on kamac"
```

List or inspect contacts:

```bash
python -m a2a_relay --base /root/agent-mailbox contacts list
python -m a2a_relay --base /root/agent-mailbox contacts show kames
```

Aliases can be used where an agent ID is accepted:

```bash
python -m a2a_relay --base /root/agent-mailbox send \
  --from lulu@kamac \
  --to kames \
  --subject "hello" \
  --body "私聊测试。"
```

Alias resolution fails safely if unknown or ambiguous.

## Mailbox layout

```text
agent-mailbox/
├── agents.json
├── contacts.json
├── inbox/
│   ├── zhiwei_known-blocks1/
│   └── lulu_kamac/
├── processing/
│   ├── zhiwei_known-blocks1/
│   └── lulu_kamac/
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

Agent IDs are mapped to safe inbox directory names by replacing non-alphanumeric
characters with `_`.

`contacts.json` stores the private contact book. Each contact entry may include:

- `allow_from` — list of agent IDs allowed to send to this contact (metadata;
  not enforced by the relay yet).
- `allowed_types` — list of message types this contact accepts (metadata; not
  enforced by the relay yet).

These fields are reserved for future policy enforcement and can be set now via
`contacts add --allow-from` / `--allowed-type`.

## Message schema: `a2a.v1`

```json
{
  "version": "a2a.v1",
  "id": "msg_20260508_083000_lulu_to_zhiwei",
  "from": "lulu@kamac",
  "to": "zhiwei@known-blocks1",
  "type": "request",
  "subject": "hello",
  "body": "知微你好，我是 lulu。",
  "created_at": "2026-05-08T08:30:00Z",
  "urgency": "normal",
  "needs_reply": true,
  "reply_to": null,
  "thread_id": "thread_lulu_zhiwei_hello",
  "attachments": [],
  "capabilities_requested": [],
  "human_approval_required": false,
  "status": "new",
  "idempotency_key": null,
  "signature": null,
  "key_id": null,
  "nonce": null,
  "signed_at": null,
  "expires_at": null
}
```

Required fields: `version`, `id`, `from`, `to`, `type`, `subject`, `body`,
`created_at`.

Message types:

- `note` — normal note
- `request` — ask another agent to do something
- `reply` — reply to a previous message
- `status` — ACK/progress/failure status
- `alert` — urgent alert
- `handoff` — task handoff
- `memory` — proposed shared memory, not auto-accepted
- `heartbeat` — liveness ping

## v0.2 reliability semantics

- Timestamps are UTC with a `Z` suffix.
- `poll` first atomically claims a file into `processing/<agent>/`.
- Invalid messages are moved to `archive/failed/` and logged.
- Processed messages are moved to `archive/processed/` with message IDs in the
  archive filename.
- `state/seen.jsonl` prevents duplicate processing by `id` and optional
  `idempotency_key`.
- Attachments are rejected in v0.2. Pass references/URLs/paths instead of binary
  payloads.
- Signing fields are reserved and preserved, but not enforced in trusted
  filesystem mode yet.

## v0.3 Dispatcher (auto-reply)

The dispatcher runs a pre-registered local command when an incoming request
passes the policy gate. Message body enters via stdin only and never selects the
command to run.

### Quick start

1. Create `dispatcher.json` in the mailbox base:

```json
{
  "agents": {
    "zhiwei@known-blocks1": {
      "enabled": true,
      "allowed_from": ["lulu@kamac"],
      "allowed_types": ["request"],
      "require_needs_reply": true,
      "default_action": "auto-reply",
      "max_body_chars": 20000,
      "stdout_max_chars": 12000
    }
  },
  "actions": {
    "auto-reply": {
      "argv": ["python", "-m", "local_agent_reply"],
      "cwd": "/srv/zhiwei-agent",
      "timeout_seconds": 120
    }
  }
}
```

2. One-shot dispatch:

```bash
python -m a2a_relay --base /root/agent-mailbox dispatch \
  --agent zhiwei@known-blocks1 \
  --action auto-reply \
  --ack
```

3. Continuous watch with dispatch:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --ack \
  --dispatch-action auto-reply
```

### Policy gate

A message is dispatched only when ALL of these conditions hold:

- Sender is a known contact
- Sender is in the agent's `allowed_from` list
- `type == "request"`
- `needs_reply` is true
- `human_approval_required` is false
- Message has not already been dispatched (dedupe by id/idempotency_key)

Messages of type `reply`, `status`, or `heartbeat` are never dispatched.

### Safety notes

- `dispatcher.json` is local-only; A2A messages cannot modify it.
- Message body never chooses which command to run; only `argv` from config is
  executed.
- Commands run with `shell=False`; no shell injection is possible.
- stderr is never included in the reply.
- stdout is bounded by `stdout_max_chars` before becoming the reply body.
- Nonzero exit or timeout logs `dispatch_failed` and sends no reply.
- `human_approval_required` messages are queued only, not dispatched; the
  claimed message remains in `processing/` for human handling instead of being
  archived.
- CLI dispatch/watch output summarizes message metadata only; it does not echo
  the incoming message body.

## Safety

A2A Relay does not execute message bodies. A receiving agent should treat
`request` messages as prompts requiring its own policy checks and, when needed,
human approval.

Do not put secrets in A2A messages. Use a secret manager or platform-native
credentials.

## Roadmap

- v0.1: filesystem mailbox, ACK, archive
- v0.2: reliable mailbox, validator, event log, dedupe, `reply`, `pending`, `threads`
- v0.3: minimal local dispatcher with policy gate
- v0.4: signatures / HMAC, nonces, replay protection
- v0.5: webhook transport
- v0.6: browser UI / timeline
- later: optional SSE/WebSocket transport

## License

MIT
