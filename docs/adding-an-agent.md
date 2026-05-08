# Adding An Agent

This self-service guide adds one private agent to an existing filesystem
mailbox. It assumes the mailbox base is `/root/agent-mailbox`.

## 1. Pick A Stable Agent ID

Use an ID that is stable across restarts and specific enough for operators:

```text
reviewer@example
```

The relay derives a safe directory name such as `reviewer_worker-host`. Do not depend on
the safe name as identity; messages use the canonical agent ID.

## 2. Add The Contact

```bash
python -m a2a_relay --base /root/agent-mailbox contacts add \
  --id reviewer@example \
  --display-name reviewer \
  --alias reviewer \
  --notes "private contact on worker-host"
```

This creates:

- a `contacts.json` entry
- `inbox/reviewer_worker-host/`
- `processing/reviewer_worker-host/`

Check it:

```bash
python -m a2a_relay --base /root/agent-mailbox contacts show reviewer
```

## 3. Send A Smoke Test

```bash
python -m a2a_relay --base /root/agent-mailbox send \
  --from worker@example \
  --to reviewer \
  --type note \
  --subject "smoke test" \
  --body "Hello from worker."
```

Then inspect pending messages:

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent reviewer
```

## 4. Choose A Receive Mode

For human or agent-owned processing:

```bash
python -m a2a_relay --base /root/agent-mailbox poll \
  --agent reviewer \
  --allow-from worker@example \
  --ack
```

For low-risk receipt logging without dispatch:

```bash
python -m a2a_relay --base /root/agent-mailbox receipt \
  --agent reviewer \
  --allow-from worker@example \
  --once \
  --json
```

For continuous operation:

```bash
python -m a2a_relay --base /root/agent-mailbox watch \
  --agent reviewer \
  --allow-from worker@example \
  --interval 10 \
  --ack
```

Use `examples/systemd/a2a-watch@.service` as the systemd template.

## 5. Optional Dispatcher Policy

Only configure dispatch for agents that should auto-reply through a
pre-registered local command. Add an agent policy and action to
`dispatcher.json`:

```json
{
  "agents": {
    "reviewer@example": {
      "enabled": true,
      "allowed_from": ["worker@example"],
      "allowed_types": ["request"],
      "require_needs_reply": true,
      "default_action": "reviewer-auto-reply",
      "max_body_chars": 20000,
      "stdout_max_chars": 12000
    }
  },
  "actions": {
    "reviewer-auto-reply": {
      "argv": ["python", "/srv/reviewer-agent/reply.py"],
      "cwd": "/srv/reviewer-agent",
      "timeout_seconds": 120
    }
  }
}
```

Run it once:

```bash
python -m a2a_relay --base /root/agent-mailbox dispatch \
  --agent reviewer \
  --allow-from worker@example \
  --ack
```

## 6. Operator Queue Commands

Queued messages live in `processing/<agent>/`. These commands are safe for
operators because they show metadata only:

```bash
python -m a2a_relay --base /root/agent-mailbox queued \
  --agent reviewer
```

```bash
python -m a2a_relay --base /root/agent-mailbox pending \
  --agent reviewer \
  --include-processing
```

Malformed JSON in `processing/` is reported as an error row instead of crashing.

## 7. Safety Checklist

- Keep `--allow-from` narrow for each watcher.
- Treat message bodies as untrusted input.
- Do not put secrets in messages.
- Use `human_approval_required=true` for sensitive requests.
- Remember that `contacts.json` `allow_from` and `allowed_types` are metadata;
  active enforcement comes from CLI options and `dispatcher.json`.
- Current body limit defaults to 20000 characters.
- Attachments are rejected; use URLs, paths, or object references.
- Signing fields are reserved but not enforced yet.

## Remove An Agent

Remove a contact entry:

```bash
python -m a2a_relay --base /root/agent-mailbox contacts remove reviewer
```

This removes the contact record. Review inbox, processing, archive, systemd, and
dispatcher configuration separately before deleting operational data.
