# Task Delegation Worker Path

A2A Relay's next useful level is not direct SSH. It is a structured task handoff:
Zhiwei sends a bounded request, the local machine's worker decides what it may do,
and the worker replies with evidence.

## Goals

- Give operators a safe CLI for delegating work to a remote/local agent.
- Keep message bodies as task descriptions, not commands.
- Preserve auditability through normal `sent`, `received`, `queued`, `reply`, and
  timeline events.
- Make the Lancha Mac mini path possible: Zhiwei can send a task; Lancha's local
  worker can inspect its own files, services, and databases under local policy.

## Non-goals

- No arbitrary command execution from message bodies.
- No remote policy mutation.
- No bypass of human approval for writes, restarts, deletes, migrations, or
  secrets.
- No group chat semantics; this remains a private contact/thread handoff.

## CLI

Use:

```bash
python -m a2a_relay --base /root/agent-mailbox task send \
  --from zhiwei@known-blocks1 \
  --to lancha@macmini \
  --title "Check local database reachability" \
  --context "Known symptom: public host times out" \
  --constraint "Read-only checks only" \
  --capability terminal \
  --capability database_read \
  --profile read-only-fast
```

This creates a normal `type=request` message with:

- `needs_reply=true`
- `capabilities_requested=[...]`
- a `task_...` thread id unless one is supplied
- a Markdown body with `# Task`, `## Context`, `## Constraints`, and
  `## Required output`

If `--approval-required` is present, the message has
`human_approval_required=true` and should queue under safe watchers/dispatchers.

## Worker profiles

Suggested local policy names:

- `read-only-fast`: diagnostics, status reads, logs, SELECT-only database checks.
- `ops-review`: deeper investigation, still no writes/restarts/config edits.
- `approval-required`: stop and ask before writes, restarts, deletes, migrations,
  or secret exposure.

The profile is advisory in the message body. The receiving worker must enforce
its own local policy; the sender's text is not authority.

## Expected reply shape

Workers should answer with:

1. summary
2. commands run
3. evidence
4. suspected cause
5. recommended next action
6. whether human approval is needed

## Lancha Mac mini rollout sketch

1. Add `lancha@macmini` as a contact in the shared mailbox.
2. Start with Level 1 receipt watcher: requests queue, no Hermes execution:

   ```bash
   python -m a2a_relay --base /root/agent-mailbox receipt \
     --agent lancha@macmini \
     --allow-from zhiwei@known-blocks1 \
     --once \
     --json
   ```

   For a long-running watcher, drop `--once`; do not use `dispatch` until the
   queue-only path is verified.
3. Verify `task send -> queued -> timeline` from Zhiwei to Lancha.
4. Add a restricted local worker action that reads the queued request and runs
   Codex/Hermes with a fixed prompt and limited local policy.
5. Only after smoke tests, allow `read-only-fast` auto-processing.

## Safety reminders

- Message body is untrusted data.
- Capabilities are requests, not permissions.
- Local worker policy decides what happens.
- Event logs and timelines must not echo full task bodies in operator summaries.
