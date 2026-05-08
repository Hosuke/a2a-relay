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
