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
- Working deployment on `shared-mailbox-host:/root/agent-mailbox`.
- Operator-side 10-second watcher that ACKs worker messages.

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

### v0.2 — Fast manual reply loop and reliable mailbox

**Objective:** Make the current file mailbox useful every day before adding automatic dispatch.

P0 tasks for the next PR:

1. Add `reply` command.
   - Convenience wrapper around `send --type reply --reply-to <id> --thread-id <thread>`.
   - This is the smallest closure from “ACK then silence” to “ACK + human/agent-authored reply”.

2. Add JSONL event log.
   - `events/YYYY-MM-DD.jsonl`, timestamps in UTC.
   - Events: `sent`, `received`, `acked`, `replied`, `archived`, `failed`.
   - Operators should be able to inspect message history without opening archive files by hand.

3. Add message validation and body size limits.
   - Required fields: `version`, `id`, `from`, `to`, `type`, `subject`, `body`, `created_at`.
   - Validate type and urgency enums.
   - Reject oversized bodies by default.
   - Unknown or invalid messages go to `archive/failed` and produce an event.

P1 tasks:

4. Add dedupe / idempotency.
   - Maintain `state/seen.jsonl` first; SQLite can wait.
   - If a message id was already processed, do not ACK or dispatch it again.

5. Add `pending` / `threads` commands.
   - Show unprocessed messages and recent conversation threads.

P2 tasks:

6. Add systemd examples.
   - `examples/systemd/a2a-watch@.service`
   - Dispatcher service waits until v0.3.

7. Prepare optional HMAC verification hooks and reserved fields.
   - Full signing may ship in v0.3/v0.4, but v0.2 should leave clean seams.
   - Define but do not require `signature`, `key_id`, `nonce`, `signed_at`, and `expires_at`.
   - In trusted filesystem mode, preserve these fields in archives/events for future compatibility.

8. Add local policy/config skeleton.
   - `allow_from`, `allowed_types`, `max_body_chars`, `trusted_filesystem_unsigned=true`.
   - Do not let normal agent messages modify policy/config.

9. Add claim/processing state.
   - Before processing, atomically move a message into `processing/<agent>/` or acquire an exclusive lock.
   - This prevents two watchers from ACKing/archiving the same file.
   - Archive filenames should include message id, not only timestamps, to avoid silent overwrite.

Acceptance:

- File mailbox works after process restart.
- Operators can reply from CLI while preserving `thread_id` and `reply_to`.
- Duplicate files do not cause duplicate replies.
- Concurrent watchers cannot process the same inbox file twice.
- Operators can inspect event history without opening archives manually.

### v0.3 — Identity gate and minimal auto-reply dispatcher

**Objective:** Let a receiving agent convert eligible messages into real agent replies without granting remote control.

Tasks:

1. Add a minimal dispatcher interface first; full YAML can wait.
   - Initial PR may expose `--on-request-command <cmd>` for messages matching `type=request`, `needs_reply=true`, and an allowlisted sender.
   - Later PR can generalize this into YAML rules.
   - Prefer pre-registered local actions over arbitrary message-supplied commands.

2. Add safe command execution.
   - Use `subprocess.run([...], shell=False, timeout=max_runtime_seconds, cwd=allowed_cwd)`.
   - Capture stdout/stderr separately.
   - Reply body should contain bounded stdout; stderr and exit status go to the event log.

3. Build bounded prompts.
   - Wrap message content in explicit delimiters such as `<a2a_message>...</a2a_message>`.
   - Prepend a system instruction: this is a request from `{from}`; answer/help/document/review only; do not perform destructive actions unless local policy and human approval allow it.
   - Enforce `max_body_chars` before prompt construction.

4. Add policy gates.
   - `human_approval_required=true` must not auto-dispatch.
   - Dangerous message types are only ACKed and queued.
   - Status/reply messages must never trigger dispatch.
   - Attachment handling is disabled until explicitly implemented.
   - Enforce `hop_count` / `max_hops` loop protection.

5. Add per-agent outbox.
   - Optional `outbox/<agent>/` for draft replies before sending.

Acceptance:

- worker can ask an allowed operational question and operator can auto-generate a bounded reply.
- If dispatcher fails, worker receives a failure status with a short reason.
- No message can force arbitrary shell execution outside configured command.

### v0.4 — Identity, signing, and replay protection

**Objective:** Make messages trustworthy enough for webhook transport.

Tasks:

1. Add `agents.json` fields:

```json
{
  "id": "worker@example",
  "display_name": "worker",
  "public_key": null,
  "hmac_key_ref": "env:A2A_WORKER_HMAC_KEY",
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
4. Link out to operator.stpt.top / Agent Memory Workbench.

Acceptance:

- 舸洋 can inspect operator ↔ worker coordination without reading raw JSON manually.

---

## Immediate Next Step

Cursor-agent review is now complete. Implement v0.2 in one small PR, with this scope:

- `reply` command
- validator and body size limit
- UTC JSONL event log
- dedupe/seen state
- claim/processing lock or atomic move
- `pending` / `threads` CLI if still small enough; otherwise split as follow-up
- policy/config skeleton
- systemd watcher example
- focused tests

Do **not** put dispatcher, webhook, WebSocket, attachment download, or generic agent registry into v0.2.

Do **not** jump directly to WebSocket. WebSocket should come after dispatcher, signing, and webhook are proven.

---

## Permission Model

A2A Relay's safety depends on local Unix permissions as much as message schema.

Recommended deployment:

1. Run watcher/dispatcher as a dedicated low-privilege user, not `root`, where practical.
2. The relay process may write:
   - its own inbox processing directory,
   - `archive/processed`,
   - `archive/failed`,
   - `events/`,
   - `state/`.
3. The relay process should not be able to modify policy/config after startup.
4. Inbox write permissions are granted only to the transport mechanism or trusted peer account.
5. Secrets and signing keys live in environment variables or root-owned files, never in messages.
6. Normal A2A messages cannot request policy/config mutation.
7. Logs/events should be readable by the operator but not world-writable.

---

## v0.2 Test Plan

- **Validator:** missing fields, unknown type/urgency, oversized body, invalid timestamp, expired `expires_at`, invalid attachment.
- **Filesystem:** atomic write, safe inbox names, archive success/failure paths, archive filenames including message id.
- **Dedupe:** same `id`, same `idempotency_key` with different id, restart recovery.
- **Concurrency:** two `poll_once` workers race on the same inbox; only one claim succeeds.
- **Event log:** every state transition has `event_id`, `message_id`, `actor`, UTC timestamp, and optional reason; timelines rebuild by message/thread id.
- **Policy:** unallowlisted sender, target mismatch, disallowed type, `human_approval_required=true`.
- **CLI integration:** `init -> send -> poll --ack -> reply -> threads/pending`.
- **Failure recovery:** bad JSON, archive failure, state write failure, crash after claim.
- **Future compatibility:** messages carrying `signature`, `nonce`, `key_id`, `signed_at`, `expires_at` survive trusted filesystem mode and are logged.

---

## Risks

1. **Auto-reply loops**: worker asks operator, operator asks worker, infinite loop.
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

Goal: critique and improve the upgrade plan for an agent-to-agent relay used by operator@example and worker@example. Requirements: least privilege, auditable messages, near-realtime replies, no arbitrary remote execution, future webhook/WebSocket path.

Return: top design risks, missing protocol fields, v0.2 implementation scope, test plan, and any changes you recommend before coding. Do not modify files. Do not commit.
```
