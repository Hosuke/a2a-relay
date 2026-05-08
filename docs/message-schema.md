# Message Schema

A2A Relay messages are JSON documents with `version: "a2a.v1"`. They are meant
to be easy to inspect, generate, validate, archive, and replay safely.

## Example

```json
{
  "version": "a2a.v1",
  "id": "msg_20260508_083000_worker_to_operator",
  "from": "worker@example",
  "to": "operator@example",
  "type": "request",
  "subject": "Check a handoff",
  "body": "Please review the queued task.",
  "created_at": "2026-05-08T08:30:00Z",
  "urgency": "normal",
  "needs_reply": true,
  "reply_to": null,
  "thread_id": "thread_worker_operator_Check_a_handoff",
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

The Python API omits fields whose values are `None` when serializing with
`A2AMessage.to_json_dict()`.

## Required Fields

- `version`: must be `a2a.v1`.
- `id`: unique non-empty string. `make_message()` generates `msg_<timestamp>_<from>_to_<to>`.
- `from`: sender agent ID.
- `to`: recipient agent ID.
- `type`: one of the supported message types below.
- `subject`: short non-empty summary.
- `body`: non-empty message body.
- `created_at`: timezone-aware ISO timestamp. The helper uses UTC with `Z`.

## Optional Fields

- `urgency`: `low`, `normal`, or `high`. Defaults to `normal`.
- `needs_reply`: boolean. Dispatcher requires this to be true for auto-reply.
- `reply_to`: original message ID when sending a reply or status.
- `thread_id`: conversation/thread ID. `make_message()` derives one if omitted.
- `attachments`: reserved list. Non-empty attachments are rejected today.
- `capabilities_requested`: reserved list for requested receiver capabilities.
- `human_approval_required`: boolean. Safe watchers and dispatcher queue these
  messages for humans instead of dispatching.
- `status`: free-form status string. The helper defaults to `new`.
- `idempotency_key`: optional dedupe key in addition to `id`.
- `signature`, `key_id`, `nonce`, `signed_at`, `expires_at`: reserved signing
  and replay-protection fields. `expires_at` is validated if present; signing is
  not enforced in trusted filesystem mode yet.

## Message Types

- `note`: a normal informational note.
- `request`: asks another agent to do work. Requests should pass receiver policy
  and may require human approval.
- `reply`: response to a prior message. Set `reply_to` and preserve `thread_id`.
- `status`: ACK, progress, or failure status.
- `alert`: urgent alert.
- `handoff`: task handoff from one agent to another.
- `memory`: proposed shared memory. Receivers should not auto-accept it.
- `heartbeat`: liveness ping.

The dispatcher only auto-dispatches eligible `request` messages. `reply`,
`status`, and `heartbeat` are never dispatched.

## Validation Rules

Current validation enforces:

- all required fields are present and non-empty strings
- `version` is `a2a.v1`
- `type` is supported and allowed by the active receiver policy
- `urgency` is `low`, `normal`, or `high`
- `from` is in the active `allow_from` set when that set is provided
- `body` length is at most `max_body_chars`
- `created_at` includes timezone data
- `expires_at`, when provided, includes timezone data and is in the future
- `needs_reply` and `human_approval_required`, when provided, are booleans
- `attachments` is empty or absent

The default validator and current CLI maximum body limit is 20000 characters.
Attachments are rejected in the current filesystem implementation; pass URLs,
paths, or external object references in `body` instead of embedding files.

## Dedupe And Threads

When a message is processed successfully, A2A Relay writes seen keys to
`state/seen.jsonl`. It dedupes by:

- `id:<message id>`
- `idem:<idempotency_key>` when `idempotency_key` is present

Use the same `thread_id` across a request, ACK/status messages, and replies.
Use `reply_to` to point at the specific message being answered.

## Safety

Message bodies are data, not commands. A receiving agent should treat `request`
body content as untrusted input and apply its own policy, approval, and tool
permissions before taking action.
