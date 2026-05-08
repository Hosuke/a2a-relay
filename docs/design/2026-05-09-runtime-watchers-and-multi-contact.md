# A2A Relay runtime watchers and multi-contact expansion

Date: 2026-05-09
Status: draft/runtime plan

## Context

A2A Relay v0.3 is merged and deployed on `shared-mailbox-host:/root/agent-mailbox`.
The current runtime has:

- contacts: `operator@example` aliases `operator,operator-alias`; `worker@example` aliases `worker,worker-alias`
- dispatcher: only `safe-echo` from `worker@example` to `operator@example`
- verified path: worker -> operator request, operator -> worker reply

The next runtime gap is not protocol shape; it is safe consumption:

- worker has pending messages, but no confirmed watcher consumes `inbox/worker_example`.
- operator can dispatch safe replies, but not yet Hermes one-shot replies.
- future contacts such as new contact should join without changing the trust model.

## Design principle

A2A is a contact/private-thread system, not a group chat.

Each agent has:

1. canonical contact id, aliases, transport, and trust level;
2. private inbox and processing/archive state;
3. local policy deciding what to auto-read, auto-ack, auto-dispatch, or queue;
4. append-only event log for operator reconstruction.

No message grants shell authority. A message may request capability; the local dispatcher decides.

## Runtime watcher levels

### Level 0: pending-only

Operator manually checks:

```bash
/root/agent-mailbox/bin/a2a pending --agent <alias>
```

This is current worker state.

### Level 1: receipt watcher

A watcher claims messages, logs them, optionally sends ACK/status, and archives or queues them.
It does not invoke Hermes.

Use for first worker-side deployment.

Safety:

- ACK only for `note/status/reply/request` from allowlisted contacts.
- Do not execute body.
- Do not send recursive ACK for `reply/status/heartbeat` unless explicitly needed.
- Unknown sender or `human_approval_required=true` stays queued.

### Level 2: one-shot summarizer

A watcher invokes Hermes with restricted toolsets and a fixed prompt only to summarize or draft a reply.
It does not run terminal/file tools unless explicitly allowed by local policy.

Suggested command shape:

```bash
hermes --profile worker chat -q '<fixed prompt with <a2a_message>...</a2a_message>>' \
  --toolsets session_search,skills -Q
```

This is for worker after Level 1 is stable.

### Level 3: task dispatcher

Only for trusted pairs and narrow capabilities.
Pre-registered actions, `shell=False`, timeout, cwd, max body, allowlist.

## Adding new contact later

Do not add him as a broadcast participant. Add as a contact:

```bash
/root/agent-mailbox/bin/a2a contacts add \
  --id <jun-agent-id> \
  --display-name 'new contact' \
  --alias new-contact \
  --trust-level trusted-filesystem-or-external \
  --allowed-type note --allowed-type request --allowed-type reply --allowed-type status
```

Then decide per-contact policy:

- Can new contact send notes only, or requests too?
- Can any request auto-dispatch, or always queue for human?
- Does new contact have his own watcher, or only manual pending?
- What identity/transport proves messages are from him?

Until signing/HMAC exists, cross-machine trusted filesystem is acceptable only when the mailbox host account boundary is trusted.
External public channels should wait for v0.4 signing or webhook HMAC.

## Immediate implementation plan

1. Add a worker-side Level 1 watcher script or systemd service on shared-mailbox-host.
2. It watches `worker@example`, allowlist `operator@example`.
3. For `note/status/reply` it claims and archives after logging; for `request` it queues unless explicitly configured.
4. It writes a concise local JSONL receipt log for worker.
5. Test with the two current pending worker messages.
6. Only after that, design Level 2 Hermes one-shot with worker profile on worker-host.

## Non-goals now

- No group chat.
- No arbitrary command execution.
- No public webhook until signatures/replay protection.
- No full Hermes auto-agent replies until receipt watcher is stable.
