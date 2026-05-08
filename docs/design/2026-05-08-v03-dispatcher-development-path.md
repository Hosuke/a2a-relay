# v0.3 Dispatcher Development Path

This note records the next one-PR path after PR #5 contacts registry.

## CLI

Add one-shot dispatcher first:

```bash
python -m a2a_relay --base /root/agent-mailbox dispatch \
  --agent zhiwei@known-blocks1 \
  --action auto-reply \
  --ack
```

Then make watch a thin loop integration:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent zhiwei@known-blocks1 \
  --allow-from lulu@kamac \
  --ack \
  --dispatch-action auto-reply
```

## Local config

Add local-only `dispatcher.json`:

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

Rules:
- ordinary A2A messages cannot modify dispatcher policy
- message body cannot choose command
- run action with `subprocess.run(argv, shell=False, timeout=..., cwd=..., text=True, capture_output=True)`
- pass message via stdin with `<a2a_message>...</a2a_message>` delimiter

## Gate

Only dispatch when all are true:
- message is private contact message
- sender resolves to known contact
- sender is allowlisted by local policy
- `type == "request"`
- `needs_reply is True`
- `human_approval_required is False`
- not already seen/dispatched by id or idempotency_key

Never dispatch `reply`, `status`, `heartbeat`.

## Events

Add:
- `dispatch_eligible`
- `dispatch_skipped`
- `dispatch_started`
- `dispatch_succeeded`
- `dispatch_failed`
- `dispatch_reply_sent`
- `dispatch_queued_for_human`

Do not log secrets, env, full prompt, or full stderr.

## Tests

Add `tests/test_dispatcher.py`:
- eligible request from allowlisted contact runs registered action and sends reply
- message body cannot choose command; body only enters stdin
- reply/status/heartbeat skipped, no subprocess
- request with `needs_reply=false` skipped
- `human_approval_required=true` queued only
- unknown or non-allowlisted sender does not run subprocess
- duplicate id/idempotency_key does not dispatch twice
- nonzero exit logs failed and sends no reply
- timeout logs failed
- stdout truncation bounded before reply
- stderr does not leak into reply
- existing v0.2 and contacts tests still pass

## Scope exclusions

Do not build group chat, webhook, WebSocket, signatures enforcement, YAML rule engine, dynamic plugins, attachment handling, shell command strings, remote policy edits, or multi-hop routing in v0.3.
