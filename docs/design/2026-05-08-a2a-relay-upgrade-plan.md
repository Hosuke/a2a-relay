# A2A Relay Upgrade Plan

> **For Hermes:** Use cursor-agent-workflow for design review first, then implement in small PR-sized increments.

**Goal:** Evolve A2A Relay from a working filesystem mailbox into a durable, auditable, multi-agent coordination layer that supports near-realtime replies, safe automation, and future webhook/WebSocket transports.

**Architecture:** Keep the core protocol transport-independent. Filesystem mailbox remains v0.1 baseline. Add reliability semantics, identity/signing, event log, dispatcher hooks, and service packaging before adding HTTP transports.

**Tech Stack:** Python stdlib first; optional FastAPI/Flask only for webhook phase; systemd examples for long-running watchers; JSON Lines for local event logs.

---

## Current State

The current repository already has:

- `a2a_relay.core`: message creation, filesystem inbox, archive helpers.
- `a2a_relay.cli`: `init`, `send`, `poll`, `watch`.
- `docs/protocol.md`: initial message schema.
- Working deployment on `lulu-jump:/root/agent-mailbox`.
- Zhiwei-side 10-second watcher that ACKs lulu messages.

The current bottleneck is not message delivery; it is **reply orchestration**. Messages are ACKed but not automatically routed into an agent run that can produce a real response.

---

## Product Direction

A2A Relay should feel like “agents adding each other as contacts,” but remain safer than chat apps or direct SSH.

Core promise:

1. An agent can send a structured request to another agent.
2. The receiver can ACK, decide policy, optionally ask a human, run work locally, and reply.
3. All steps remain inspectable by humans.
4. No agent receives arbitrary remote execution rights over another machine.

---

## Design Principles

1. **Messages over control**: message body is never executed directly.
2. **Transport independent**: one schema for filesystem, webhook, queue, WebSocket/SSE.
3. **At-least-once with idempotency**: duplicate delivery is acceptable; duplicate side effects are not.
4. **Human-auditable**: JSON messages and JSONL events can be read with basic tools.
5. **Least privilege**: per-agent inbox permissions; sender allowlists; no secrets in messages.
6. **Policy before action**: receiver decides what message types can auto-run.
7. **Graceful degradation**: file mailbox remains the fallback even after webhook exists.

---

## Recommended Roadmap

### v0.2 — Reliable mailbox and service hardening

**Objective:** Make the current file mailbox robust enough for daily agent use.

Tasks:

1. Add message validation.
   - Required fields: `version`, `id`, `from`, `to`, `type`, `subject`, `body`, `created_at`.
   - Validate type and urgency enums.
   - Reject oversized bodies by default.

2. Add dedupe / idempotency.
   - Maintain `state/seen.jsonl` or `state/seen.sqlite`.
   - If a message id was already processed, do not ACK again unless explicitly requested.

3. Add JSONL event log.
   - `events/YYYY-MM-DD.jsonl`
   - Events: `sent`, `received`, `acked`, `archived`, `failed`, `dispatched`, `replied`.

4. Add systemd examples.
   - `examples/systemd/a2a-watch@.service`
   - `examples/systemd/a2a-dispatch@.service`

5. Add `reply` command.
   - Convenience wrapper around `send --type reply --reply-to <id> --thread-id <thread>`.

6. Add `pending` / `threads` commands.
   - Show unprocessed messages and recent conversation threads.

Acceptance:

- File mailbox works after process restart.
- Duplicate files do not cause duplicate replies.
- Operators can inspect event history without opening archives manually.

### v0.3 — Dispatcher hooks and auto-reply orchestration

**Objective:** Let a receiving agent convert eligible messages into real agent replies.

Tasks:

1. Add dispatcher configuration.

```yaml
agent: zhiwei@known-blocks1
allow_from:
  - lulu@kamac
rules:
  - match:
      type: request
      needs_reply: true
    action: command
    command:
      - /opt/hermes-agent/venv/bin/hermes
      - run
      - --prompt-file
      - "{prompt_file}"
    require_human_approval: false
limits:
  max_body_chars: 20000
  max_runtime_seconds: 600
```

2. Add `dispatch` command.
   - Reads a message.
   - Builds a prompt with metadata + body.
   - Runs configured command.
   - Captures stdout/stderr.
   - Sends reply or failure status.

3. Add policy gates.
   - `human_approval_required=true` must not auto-dispatch.
   - Dangerous message types are only ACKed and queued.
   - Attachment handling is disabled until explicitly implemented.

4. Add per-agent outbox.
   - Optional `outbox/<agent>/` for draft replies before sending.

Acceptance:

- lulu can ask “IMA 旧条目怎么删?” and zhiwei can auto-generate a bounded reply.
- If dispatcher fails, lulu receives a failure status with a short reason.
- No message can force arbitrary shell execution outside configured command.

### v0.4 — Identity, signing, and replay protection

**Objective:** Make messages trustworthy enough for webhook transport.

Tasks:

1. Add `agents.json` fields:

```json
{
  "id": "lulu@kamac",
  "display_name": "lulu",
  "public_key": null,
  "hmac_key_ref": "env:A2A_LULU_HMAC_KEY",
  "allowed_types": ["note", "request", "reply", "status"],
  "max_urgency": "high"
}
```

2. Add HMAC signing.
   - Canonicalize JSON excluding `signature`.
   - Add `signature`, `nonce`, `expires_at`.

3. Add verify path in `poll` / webhook.
   - Reject expired messages.
   - Reject reused nonce.
   - Archive invalid messages to `archive/failed` with reason.

Acceptance:

- File transport can run unsigned in trusted mode.
- Webhook transport requires signatures by default.

### v0.5 — Webhook transport

**Objective:** Reduce latency and remove SSH polling where appropriate.

Tasks:

1. Add minimal HTTP receiver.
   - `POST /api/a2a/inbox`
   - HMAC required.
   - Stores message into same inbox/event log.

2. Add sender transport abstraction.
   - `filesystem://...`
   - `https://...`

3. Add rate limit and body limits.

4. Add delivery retries with exponential backoff.

Acceptance:

- Filesystem and webhook transports produce identical stored messages.
- If webhook fails, sender can fall back to file mailbox.

### v0.6 — Human timeline UI

**Objective:** Let humans see what agents said, what was ACKed, what was acted upon, and why.

Tasks:

1. Static/read-only timeline UI over JSONL events.
2. Thread view by `thread_id`.
3. Filters: agent, urgency, needs_reply, failed.
4. Link out to zhiwei.stpt.top / Agent Memory Workbench.

Acceptance:

- 舸洋 can inspect zhiwei ↔ lulu coordination without reading raw JSON manually.

---

## Immediate Next Step

Use cursor-agent for a design review before coding. Ask two reviewers:

1. GPT-5.5: protocol and reliability review.
2. Opus: product/architecture review.

Then implement v0.2 in one PR:

- validator
- event log
- dedupe state
- reply command
- systemd examples
- focused tests

Do **not** jump directly to WebSocket. WebSocket should come after dispatcher, signing, and webhook are proven.

---

## Risks

1. **Auto-reply loops**: lulu asks zhiwei, zhiwei asks lulu, infinite loop.
   - Mitigation: `max_hops`, `reply_to`, loop detection, no auto-dispatch on `status`.

2. **Secret leakage**: agents paste credentials into messages.
   - Mitigation: docs, regex warning, optional redaction hook.

3. **Confused deputy**: one agent asks another to run privileged actions.
   - Mitigation: local policy gates and human approval.

4. **Duplicate delivery**: polling sees same file twice.
   - Mitigation: seen-state and atomic archive.

5. **Overengineering too early**.
   - Mitigation: keep filesystem transport first; add only reliability and dispatcher next.

---

## Cursor-Agent Review Prompt

```text
You are reviewing Hosuke/a2a-relay. Read README.md, docs/protocol.md, a2a_relay/core.py, a2a_relay/cli.py, and docs/design/2026-05-08-a2a-relay-upgrade-plan.md.

Goal: critique and improve the upgrade plan for an agent-to-agent relay used by zhiwei@known-blocks1 and lulu@kamac. Requirements: least privilege, auditable messages, near-realtime replies, no arbitrary remote execution, future webhook/WebSocket path.

Return: top design risks, missing protocol fields, v0.2 implementation scope, test plan, and any changes you recommend before coding. Do not modify files. Do not commit.
```
