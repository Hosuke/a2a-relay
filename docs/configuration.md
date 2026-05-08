# Configuration

A2A Relay uses JSON files under the mailbox base directory. The two operator
facing files are:

- `contacts.json` for private contact discovery and aliases
- `dispatcher.json` for policy-gated local auto-reply actions

`contacts.json` is created by `init`. `dispatcher.json` is optional and must be
created by the operator when dispatch is needed.

## contacts.json

`contacts.json` has one top-level `contacts` object keyed by stable agent ID:

```json
{
  "contacts": {
    "worker@example": {
      "id": "worker@example",
      "display_name": "worker",
      "aliases": ["worker"],
      "transport": "filesystem",
      "inbox": "/root/agent-mailbox/inbox/worker_example",
      "allow_from": [],
      "allowed_types": [
        "alert",
        "handoff",
        "heartbeat",
        "memory",
        "note",
        "reply",
        "request",
        "status"
      ],
      "trust_level": "trusted-filesystem",
      "notes": "local private contact",
      "safe_name": "worker_example"
    }
  }
}
```

### Contact Fields

- `id`: stable agent ID. This is the canonical identity used in messages.
- `display_name`: optional human-readable name.
- `aliases`: optional alternative names accepted by CLI commands.
- `transport`: currently `filesystem`.
- `inbox`: filesystem inbox path. If omitted when created by Python API,
  `Contact.to_json_dict(base)` fills it from the mailbox base.
- `allow_from`: contact metadata describing expected senders.
- `allowed_types`: contact metadata describing expected message types.
- `trust_level`: operator label, usually `trusted-filesystem`.
- `notes`: free-form operator notes.
- `safe_name`: derived filesystem-safe name.

`allow_from` and `allowed_types` in `contacts.json` are metadata for humans and
future policy. Current enforcement happens through `poll`, `watch`, `receipt`,
and `dispatch` CLI options plus `dispatcher.json` policy. Do not assume a value
in `contacts.json` blocks messages by itself.

### Contact Commands

Add a contact:

```bash
python -m a2a_relay --base /root/agent-mailbox contacts add \
  --id reviewer@example \
  --display-name reviewer \
  --alias reviewer \
  --alias kam \
  --notes "private contact on worker-host"
```

List and inspect contacts:

```bash
python -m a2a_relay --base /root/agent-mailbox contacts list
python -m a2a_relay --base /root/agent-mailbox contacts show kam
```

Aliases can be used where the CLI accepts an agent ID. Unknown or ambiguous
aliases fail before a message is written.

## dispatcher.json

`dispatcher.json` controls whether an incoming request can run a pre-registered
local command and send stdout back as a reply.

```json
{
  "agents": {
    "operator@example": {
      "enabled": true,
      "allowed_from": ["worker@example"],
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
      "cwd": "/srv/operator-agent",
      "timeout_seconds": 120
    }
  }
}
```

### Agent Policy Fields

- `enabled`: dispatch is skipped unless this is true.
- `allowed_from`: required non-empty list of sender IDs or aliases allowed to
  trigger dispatch.
- `allowed_types`: documented policy intent. Current dispatcher code only
  dispatches `request`; `reply`, `status`, and `heartbeat` are never dispatched.
- `require_needs_reply`: documented policy intent. Current dispatcher requires
  `needs_reply=true`.
- `default_action`: action name used when the CLI does not pass `--action` or
  `--dispatch-action`.
- `max_body_chars`: documented policy intent. Current validator limit is set by
  CLI `--max-body-chars`, default 20000.
- `stdout_max_chars`: maximum stdout characters copied into the reply body,
  default 12000.

### Action Fields

- `argv`: required command argv list. It is executed with `shell=False`.
- `cwd`: optional working directory for the action.
- `timeout_seconds`: optional timeout, default 120 seconds.

The incoming message body is passed to the action over stdin in a delimited
prompt. Message body never chooses the command. stderr is not included in the
reply. Nonzero exit and timeout log a dispatch failure and send no reply.

### Dispatch Eligibility

A message is dispatched only when all of these are true:

- The recipient has an enabled policy in `dispatcher.json`.
- The sender is a known contact.
- The sender resolves into the policy `allowed_from` list.
- `type` is `request`.
- `needs_reply` is true.
- `human_approval_required` is false.
- The message has not already been seen by `id` or `idempotency_key`.

Messages that require human approval are left in `processing/<agent>/` and are
visible with:

```bash
python -m a2a_relay --base /root/agent-mailbox queued \
  --agent operator@example
```

or:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent operator@example \
  --include-processing
```

## Validation Defaults

Current schema validation rejects:

- unsupported `version`
- missing required fields
- unknown message `type`
- unknown `urgency`
- senders outside the active `--allow-from` policy
- body over the active maximum, default 20000 characters
- non-empty `attachments`
- expired `expires_at`
- non-boolean `needs_reply` or `human_approval_required`

Signing fields are reserved in the schema but are not enforced in the current
trusted filesystem mode.

## Example Files

See:

- `examples/config/minimal-contacts.json`
- `examples/config/minimal-dispatcher.json`
