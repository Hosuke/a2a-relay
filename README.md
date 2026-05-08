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

> write a signed/structured message into the other agent's inbox, get an ACK,
> archive the original.

## Design principles

- **Messages, not remote control** — agents exchange requests and replies, not
  arbitrary command execution.
- **Transport-independent protocol** — the same JSON message can travel through
  files, webhooks, queues, or WebSockets.
- **Auditable by humans** — every message is plain JSON on disk.
- **Least privilege** — the shared mailbox can run on a jump host with restricted
  users.
- **Realtime enough first** — 5–10 second polling is often better than a fragile
  realtime stack.

## Quick start: filesystem mailbox

Create a mailbox root on a shared host:

```bash
python -m a2a_relay init --base /root/agent-mailbox \
  --agent zhiwei@known-blocks1 --agent lulu@kamac
```

Send a message:

```bash
python -m a2a_relay send \
  --base /root/agent-mailbox \
  --from lulu@kamac \
  --to zhiwei@known-blocks1 \
  --type note \
  --subject "hello" \
  --body "知微你好，我是 lulu。"
```

Poll an inbox and ACK messages:

```bash
python -m a2a_relay poll \
  --base /root/agent-mailbox \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --ack
```

Run near-realtime polling:

```bash
python -m a2a_relay watch \
  --base /root/agent-mailbox \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --interval 10 \
  --ack
```

## Mailbox layout

```text
agent-mailbox/
├── agents.json
├── inbox/
│   ├── zhiwei/
│   └── lulu/
├── archive/
│   ├── processed/
│   └── failed/
├── logs/
├── tmp/
└── README.md
```

Agent IDs are mapped to safe inbox directory names by replacing non-alphanumeric
characters with `_`.

## Message schema: `a2a.v1`

```json
{
  "version": "a2a.v1",
  "id": "msg_20260508_163000_lulu_to_zhiwei",
  "from": "lulu@kamac",
  "to": "zhiwei@known-blocks1",
  "type": "note",
  "subject": "hello",
  "body": "知微你好，我是 lulu。",
  "created_at": "2026-05-08T16:30:00+08:00",
  "urgency": "normal",
  "needs_reply": true,
  "reply_to": null,
  "thread_id": "thread_lulu_zhiwei_hello",
  "attachments": [],
  "capabilities_requested": [],
  "human_approval_required": false,
  "status": "new"
}
```

Message types:

- `note` — normal note
- `request` — ask another agent to do something
- `reply` — reply to a previous message
- `status` — ACK/progress/failure status
- `alert` — urgent alert
- `handoff` — task handoff
- `memory` — proposed shared memory, not auto-accepted
- `heartbeat` — liveness ping

## Safety

A2A Relay does not execute message bodies. A receiving agent should treat
`request` messages as prompts requiring its own policy checks and, when needed,
human approval.

Do not put secrets in A2A messages. Use a secret manager or platform-native
credentials.

## Roadmap

- v0.1: filesystem mailbox, ACK, archive
- v0.2: signatures / HMAC, nonces, replay protection
- v0.3: webhook transport
- v0.4: browser UI / timeline
- v0.5: optional SSE/WebSocket transport

## License

MIT
