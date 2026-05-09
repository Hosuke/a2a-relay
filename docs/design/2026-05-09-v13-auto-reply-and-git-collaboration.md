# v1.3 Design: Safe Auto-Reply and Git Collaboration Workflow

Date: 2026-05-09
Status: draft
Authors: operator design lead, agent-assisted

---

## 1. Motivation

A2A Relay v0.3 ships a policy-gated dispatcher that runs pre-registered local
commands. The v1.3 gap is narrow private automation: allowlisted agent pairs
should be able to exchange bounded requests and produce real replies for
low-risk threads, while preserving auditability and keeping public integrations
out of the automatic loop.

This document designs v1.3 around a narrow release core plus explicit
post-v1.3 design notes:

1. **Safe automatic replies** between private, allowlisted agent pairs.
2. **Staged safety levels** from receipt watcher to constrained one-shot agent.
3. **Public/private boundary** enforcement for docs, examples, and runtime logs.
4. **Protocol and audit hooks** that make later GitHub and multi-agent work
   possible without shipping that automation in v1.3.
5. **Postponed design sketches** for GitHub PR interaction and multi-agent
   orchestration. These sketches are not v1.3 release commitments.

### Versioning note

The jump from v0.3 to v1.3 reflects three maturity stages planned but not
individually released (v1.0 stable mailbox, v1.1 multi-agent watcher, v1.2
task delegation runtime). v1.3 is the first milestone that enables autonomous
private agent-to-agent collaboration end to end.

### Release boundary

v1.3 ships only private-contact, policy-gated automation. Public GitHub
automation, automatic PR comments, code changes from PR text, multi-agent write
orchestration, recursive delegation, automatic pushes/merges, and public
webhook ingestion are **postponed** until later releases with separate threat
models and approval gates.

---

## 2. Scope

### In scope

- Policy schema changes for multi-pair auto-reply.
- Dispatcher `agent_runner` action type only for constrained private runs.
- Read-only or draft-only hooks needed to support future GitHub review flows.
- Five staged safety levels with clear promotion criteria.
- CLI UX for configuring, dry-running, and monitoring auto-reply pairs.
- Acceptance tests, failure modes, and rollback plan.
- Documentation that clearly separates v1.3 release scope from postponed
  GitHub and multi-agent write automation.

### Out of scope

- Webhook/WebSocket transport (v0.5).
- HMAC signing enforcement (v0.4).
- Browser timeline UI (v0.6).
- Group chat semantics.
- Arbitrary remote command execution.
- Public GitHub webhook automation.
- Automatic posting of agent-generated content to GitHub.
- GitHub-triggered code edits or branch pushes.
- Automatic merges, releases, force pushes, restarts, migrations, or deletes.
- Multi-agent write orchestration and recursive delegation.

---

## 3. Agent Naming Convention (Public/Private Boundary)

Public documentation and committed code use **generic agent names**:

| Public name       | Role                           |
|-------------------|--------------------------------|
| `alpha@example`   | Primary operator agent         |
| `bravo@example`   | Primary worker agent           |
| `charlie@example` | Secondary operator agent       |
| `delta@example`   | Secondary worker agent         |

Real agent IDs, hostnames, tunnel ports, mailbox paths, and deployment details
belong in `local/` (gitignored) only. The public `dispatcher.json` examples
and test fixtures use `@example` suffixed IDs exclusively.

**Rule**: if a string would let a reader identify a private machine, person,
or credential, it goes in `local/`, never in committed docs or code.

---

## 4. Safe Automatic Reply Architecture

### 4.1 Agent Pairs

v1.3 targets two independent auto-reply pairs:

```
Pair A:  alpha@example  <-->  bravo@example
Pair B:  charlie@example  <-->  delta@example
```

Each pair is configured independently in `dispatcher.json`. Cross-pair
messaging is permitted but never auto-dispatched; it queues for human review.

### 4.2 Policy Schema Changes

`dispatcher.json` gains new fields (backward compatible; old configs still
work):

```json
{
  "version": "dispatcher.v2",
  "agents": {
    "alpha@example": {
      "enabled": true,
      "allowed_from": ["bravo@example"],
      "allowed_types": ["request"],
      "require_needs_reply": true,
      "default_action": "agent-reply",
      "max_body_chars": 20000,
      "stdout_max_chars": 12000,
      "safety_level": "receipt",
      "max_replies_per_thread": 5,
      "max_auto_threads_per_hour": 10,
      "cooldown_seconds": 30,
      "allowed_capabilities": ["terminal_read", "file_read", "database_select"],
      "blocked_keywords_in_body": [],
      "require_thread_id": true
    }
  },
  "actions": {
    "agent-reply": {
      "type": "agent_runner",
      "runner": "cursor-agent",
      "model": "opus-4.6",
      "profile": "read-only-fast",
      "argv_fallback": ["python", "-m", "local_agent_reply"],
      "cwd": "/srv/agent-workspace",
      "timeout_seconds": 300,
      "max_tool_calls": 20,
      "sandbox": true
    }
  },
  "review": {
    "enabled": false,
    "runner": "cursor-agent",
    "model": "gpt-5.5",
    "trigger": "on_reply_before_send",
    "max_review_chars": 8000,
    "auto_approve_safety_levels": ["receipt", "ack_only"]
  },
  "rate_limits": {
    "global_max_dispatches_per_hour": 60,
    "global_max_replies_per_hour": 120
  }
}
```

#### New fields explained

| Field                        | Type      | Purpose                                                    |
|------------------------------|-----------|------------------------------------------------------------|
| `version`                    | string    | Config version for migration. Optional; defaults to `v1`.  |
| `safety_level`               | string    | Current safety level for this agent (see §7).              |
| `max_replies_per_thread`     | int       | Loop protection: stop auto-reply after N replies/thread.   |
| `max_auto_threads_per_hour`  | int       | Runaway protection: cap new auto-threads per hour.         |
| `cooldown_seconds`           | int       | Minimum gap between dispatches for one agent.              |
| `allowed_capabilities`       | list[str] | Capabilities the agent runner may use.                     |
| `blocked_keywords_in_body`   | list[str] | Body keyword blocklist; match triggers queue-for-human.    |
| `require_thread_id`          | bool      | Reject messages without thread_id from auto-dispatch.      |
| `type` (action)              | string    | `"subprocess"` (default, current) or `"agent_runner"`.     |
| `runner` (action)            | string    | Runner backend: `cursor-agent`, `codex`, or `subprocess`.  |
| `model` (action)             | string    | Model identifier for agent_runner actions.                 |
| `profile` (action)           | string    | Worker profile: `read-only-fast`, `ops-review`, etc.       |
| `argv_fallback` (action)     | list[str] | Subprocess fallback if agent_runner is unavailable.        |
| `max_tool_calls` (action)    | int       | Bound on tool invocations per agent run.                   |
| `sandbox` (action)           | bool      | Run agent in sandboxed mode (no write, no network).        |
| `review`                     | object    | Optional review pass config (see §6).                      |
| `rate_limits`                | object    | Global rate limits across all agents.                      |

### 4.3 Loop Protection

Auto-reply loops are the primary runaway risk. Protections:

1. **Thread reply cap** (`max_replies_per_thread`): the dispatcher counts
   existing `dispatch_reply_sent` events for the thread. If count >=
   threshold, the message is queued for human.
2. **Hourly thread cap** (`max_auto_threads_per_hour`): limits new
   auto-dispatched threads to prevent flood.
3. **Cooldown** (`cooldown_seconds`): enforces a minimum interval between
   dispatches for the same agent. Checked against the last
   `dispatch_started` event timestamp.
4. **Never dispatch reply/status/heartbeat**: existing v0.3 invariant
   preserved.
5. **Global rate limits**: hard ceiling across all agents.

### 4.4 Keyword Blocklist

`blocked_keywords_in_body` is a list of case-insensitive substrings. If any
match appears in the message body, the message is queued for human review
with reason `blocked_keyword:<match>`. Use this for secret patterns,
destructive verbs, or org-specific terms.

---

## 5. GitHub-Based Design/PR Interaction Workflow (Postponed)

This section is a post-v1.3 design sketch. v1.3 may record metadata that links a
private mailbox thread to a PR, but it must not automatically post to GitHub,
edit code from PR comments, merge PRs, or treat GitHub text as trusted input.

### 5.1 Overview

```
 Agent A (alpha)       Human-approved bridge       GitHub / Agent B
 ─────────────────     ─────────────────────       ────────────────
 drafts private note → proposes PR metadata
                       operator approves       → opens PR or requests review
 receives note      ← relays approved summary
 operator approves  → posts or pushes outside the automatic v1.3 loop
```

### 5.2 A2A ↔ GitHub Bridge

A future action type `github_pr` in `dispatcher.json` could connect A2A threads
to GitHub PRs after signature verification, replay protection, repo/actor
allowlists, and human approval gates exist:

```json
{
  "type": "github_pr",
  "repo": "owner/a2a-relay",
  "base_branch": "main",
  "mode": "metadata_only",
  "auto_create_pr": false,
  "human_approval_required": true,
  "pr_title_template": "[A2A] {subject}",
  "pr_body_from_message": false,
  "reviewers": ["bravo-bot"],
  "labels": ["a2a-auto"]
}
```

#### Future workflow steps

These steps are postponed. They describe the shape of a later release, not the
v1.3 shipping behavior.

1. **Design request** arrives as a `type=request` A2A message with
   `capabilities_requested: ["github_pr"]`.
2. The controller proposes a feature branch name:
   `a2a/{thread_id_short}/{safe_subject}`.
3. The agent runner drafts a private design note or review summary.
4. A human-approved controller opens a PR using `gh pr create`.
5. An A2A `status` message is sent back with the PR URL and status.
6. The review agent receives a review-request A2A message and drafts a review
   for human approval.
7. Human-approved review comments may be relayed back as A2A `note` messages.
8. Any implementation changes, pushes, merges, or closeout replies require
   explicit human approval outside v1.3 automation.

### 5.3 PR Metadata in Messages

Messages related to a PR carry structured metadata in the body:

```markdown
## PR Reference
- repo: owner/a2a-relay
- pr: #42
- branch: a2a/thread_abc/design-v13
- status: open | review_requested | changes_requested | approved | merged
- ci: passing | failing | pending
```

This is body content, not schema fields, preserving v1 schema compatibility.

### 5.4 CLI for GitHub Integration (Illustrative, Postponed)

The following commands are illustrative. They should not be implemented as
automatic v1.3 behavior unless they are read-only/draft-only and gated by human
approval before any public GitHub write.

```bash
# Create a design branch and PR from an A2A request
a2a-relay --base $BASE github create-branch \
  --thread-id thread_alpha_bravo_design \
  --repo owner/a2a-relay

# Relay a GitHub review back as an A2A note
a2a-relay --base $BASE github relay-review \
  --pr 42 \
  --to alpha@example

# Check PR status and update A2A thread
a2a-relay --base $BASE github sync-status \
  --thread-id thread_alpha_bravo_design
```

---

## 6. Multi-Cursor-Agent Orchestration (Postponed Beyond Draft-Only Runs)

v1.3 may define the policy and audit vocabulary for orchestration, but
multi-agent write workflows are postponed. Any v1.3 agent run must be private,
bounded by local policy, and limited to read-only or draft-only behavior unless
an operator explicitly approves the write outside the automatic loop.

### 6.1 Two-Model Architecture

```
                   ┌──────────────────────┐
                   │   Orchestrator       │
                   │   (dispatcher loop)  │
                   └──────┬───────┬───────┘
                          │       │
              ┌───────────▼─┐   ┌─▼───────────┐
              │ Opus 4.6    │   │ GPT-5.5     │
              │ Implement   │   │ Review      │
              │ agent_runner │   │ agent_runner │
              └─────────────┘   └─────────────┘
```

- **Implementation agent** handles draft work only in v1.3: reading, planning,
  or preparing proposed changes under a bounded profile. Writing code, docs,
  config changes, or pushing branches is postponed unless an operator approves
  the run outside the automatic loop.
- **GPT-5.5** handles review: reading diffs, evaluating correctness, safety,
  and style. It operates in read-only mode with no tool-call write
  permissions.

### 6.2 Orchestrator Flow

```
1. Incoming request → policy gate → dispatch_eligible
2. Agent runner produces read-only findings or a draft reply
3. Draft output → review/redaction gate
4. If approved by policy → send private reply
5. If changes requested → queue for human or re-run once under the same policy
6. If still failing → queue for human with review notes
```

### 6.3 Orchestrator Config

The `review` section in `dispatcher.json` (§4.2) controls the review pass:

- `trigger: "on_reply_before_send"` — review runs after implementation but
  before the reply is sent to the requesting agent.
- `trigger: "on_pr_ready"` — review runs only when a PR is created.
- `trigger: "disabled"` — no automatic review.

### 6.4 Agent Runner Interface

The `agent_runner` action type replaces subprocess for capable backends:

```python
class AgentRunnerAction:
    runner: str          # "cursor-agent" | "codex" | "subprocess"
    model: str           # "opus-4.6" | "gpt-5.5" | ...
    profile: str         # worker profile name
    sandbox: bool        # sandbox mode
    timeout_seconds: int
    max_tool_calls: int

    def run(self, prompt: str, context: dict) -> AgentRunnerResult:
        """
        prompt: the a2a_message delimited prompt
        context: thread history, PR metadata, etc.
        returns: AgentRunnerResult with stdout, artifacts, exit_code
        """
```

The interface is intentionally similar to `run_action` in `dispatcher.py`.
The agent runner receives the same `<a2a_message>` delimited stdin. It may
also receive a `<a2a_context>` block with thread history (metadata only, no
bodies from prior messages unless the thread is short and within body size
limits).

### 6.5 Model Fallback

If the configured model is unavailable (API error, rate limit), the
dispatcher falls back:

1. Try configured `model`.
2. If unavailable, try `argv_fallback` subprocess.
3. If both fail, log `dispatch_failed` and leave the message queued.

No silent model substitution. The event log records which model was attempted.

---

## 7. Staged Safety Levels

Each agent has a `safety_level` in its dispatcher policy. Promotion from one
level to the next requires passing the acceptance criteria for that level.

### Level 0: `manual`

The agent has no watcher. Operator checks `pending` manually.

**Acceptance**: mailbox exists, contacts configured, `send` and `pending`
work.

### Level 1: `receipt`

Receipt watcher claims, validates, logs, and archives low-risk messages.
Requests stay in `processing/` for human handling.

```bash
a2a-relay --base $BASE receipt \
  --agent bravo@example \
  --allow-from alpha@example \
  --json
```

**What happens**: `note`, `status`, `reply`, `heartbeat` are archived.
`request` is queued. `human_approval_required=true` is always queued.

**Acceptance criteria**:
- Receipt watcher runs for 24h without error.
- All expected message types are correctly routed.
- `doctor` shows clean health.
- Event log shows receipt/archive events.

### Level 2: `ack_only`

Watcher claims and sends an ACK status reply for eligible requests, but does
not invoke any agent runner or subprocess. The request body is not processed.

```bash
a2a-relay --base $BASE watch \
  --agent bravo@example \
  --allow-from alpha@example \
  --ack \
  --interval 10
```

**Acceptance criteria**:
- Level 1 criteria plus:
- ACK delivery verified in requesting agent's inbox.
- No subprocess or agent runner invoked.
- `threads --needs-reply` correctly shows unresolved threads.

### Level 3: `echo_dispatch`

Dispatcher invokes a trivial echo action (pre-registered, not agent runner).
Validates the full dispatch pipeline without real agent work.

```bash
a2a-relay --base $BASE watch \
  --agent bravo@example \
  --allow-from alpha@example \
  --ack \
  --dispatch-action safe-echo
```

`safe-echo` action:

```json
{
  "argv": ["echo", "received and echoed"],
  "timeout_seconds": 10
}
```

**Acceptance criteria**:
- Level 2 criteria plus:
- Dispatch events logged: `dispatch_eligible`, `dispatch_started`,
  `dispatch_succeeded`, `dispatch_reply_sent`.
- Reply arrives in the requester's inbox.
- Timeout and failure paths tested.
- Loop protection verified: thread with 5+ replies stops dispatching.

### Level 4: `constrained_agent`

Dispatcher invokes an agent runner (Opus 4.6) in sandboxed, read-only mode
with bounded tool calls.

```json
{
  "type": "agent_runner",
  "runner": "cursor-agent",
  "model": "opus-4.6",
  "profile": "read-only-fast",
  "timeout_seconds": 300,
  "max_tool_calls": 20,
  "sandbox": true
}
```

**Acceptance criteria**:
- Level 3 criteria plus:
- Agent produces a meaningful reply (not just echo).
- No write operations observed in sandbox mode.
- stdout bounded by `stdout_max_chars`.
- Timeout triggers clean `dispatch_failed`.
- Model fallback to `argv_fallback` tested.
- Review pass (GPT-5.5) runs and produces structured review.
- Rate limits respected.
- 10 consecutive successful dispatches without human intervention.

### Level 5: `constrained_agent_with_writes`

Postponed for v1.3. Agent runner may eventually perform bounded writes (create
files, push commits) under an `ops-review` profile, but this level must remain
disabled in v1.3 automatic promotion. It is only reachable in a later release
after Level 4 is stable and write-specific approval, worktree, audit, and
rollback controls are implemented.

**Acceptance criteria**:
- Level 4 criteria plus:
- Write operations are within `allowed_capabilities`.
- All writes are auditable through event log and git history.
- PR creation and review cycle completes end to end.
- `human_approval_required` correctly blocks destructive requests.

### Level Promotion CLI

```bash
# Show current safety level
a2a-relay --base $BASE safety-level show --agent bravo@example

# Promote (validates acceptance criteria)
a2a-relay --base $BASE safety-level promote \
  --agent bravo@example \
  --to constrained_agent \
  --dry-run

# Demote (immediate, no validation)
a2a-relay --base $BASE safety-level demote \
  --agent bravo@example \
  --to receipt \
  --reason "dispatch failure spike"
```

Promotion writes a `safety_level_changed` event. Demotion is always instant.

---

## 8. CLI UX Summary

### New commands

| Command                          | Purpose                                    |
|----------------------------------|--------------------------------------------|
| `safety-level show`              | Show current safety level for an agent     |
| `safety-level promote`           | Promote with acceptance check              |
| `safety-level demote`            | Demote immediately                         |
| `github create-branch`           | Post-v1.3: human-approved branch creation  |
| `github relay-review`            | Post-v1.3: human-approved review relay     |
| `github sync-status`             | Post-v1.3: metadata-only PR status sync    |
| `dispatch --dry-run`             | Preview dispatch decision without running  |
| `dispatch --action agent-reply`  | Dispatch via agent_runner                  |

### Modified commands

| Command              | Change                                              |
|----------------------|-----------------------------------------------------|
| `watch`              | Respects `safety_level`, rate limits, cooldown       |
| `dispatch`           | Supports `agent_runner` action type                  |
| `threads`            | New `--auto-replied` filter                          |
| `doctor`             | Reports safety level, rate limit headroom, loop risk |

### Dry-run support

Every dispatch path supports `--dry-run` which logs the decision but does
not invoke the action or send a reply:

```bash
a2a-relay --base $BASE dispatch \
  --agent bravo@example \
  --action agent-reply \
  --dry-run
```

Output:

```json
{
  "would_dispatch": true,
  "agent": "bravo@example",
  "action": "agent-reply",
  "safety_level": "constrained_agent",
  "thread_replies_so_far": 2,
  "cooldown_remaining_seconds": 0,
  "rate_limit_remaining": 55,
  "blocked_keywords_matched": [],
  "review_enabled": true
}
```

---

## 9. Implementation Milestones

Release scope warning: Milestones 1-2 and the read-only/draft-only subset of
Milestone 3 are v1.3 candidates. Milestones 4-6 are design backlog unless each
item is narrowed to private, read-only/draft-only behavior with explicit human
approval for external writes.

### Milestone 1: Policy schema v2 (Week 1)

- Add new `dispatcher.json` fields with backward-compatible defaults.
- Add `safety_level` to agent policy; default to `"manual"`.
- Add `rate_limits` section.
- Add `safety-level show` / `promote` / `demote` CLI.
- Add schema migration: old configs auto-fill new fields with safe defaults.
- Tests: schema validation, migration, level transitions.

### Milestone 2: Loop protection and rate limits (Week 1–2)

- Implement `max_replies_per_thread` check in `check_policy_gate`.
- Implement `max_auto_threads_per_hour` with sliding window over events.
- Implement `cooldown_seconds` check.
- Implement `blocked_keywords_in_body`.
- Implement `require_thread_id`.
- Implement global rate limits.
- Add `--dry-run` to `dispatch`.
- Tests: loop cap, rate limits, cooldown, keyword blocklist, dry-run.

### Milestone 3: Agent runner action type (Week 2–3)

- Add `AgentRunnerAction` interface in `dispatcher.py`.
- Implement `cursor-agent` backend (spawn cursor-agent CLI subprocess).
- Implement model fallback to `argv_fallback`.
- Add `sandbox` mode enforcement.
- Add `max_tool_calls` enforcement.
- Add `profile` injection into agent prompt.
- Tests: agent runner invocation, sandbox, fallback, timeout.

### Milestone 4: Review pass (Post-v1.3 unless draft-only)

- Add `review` section processing in dispatch pipeline.
- Implement `on_reply_before_send` trigger: after Opus 4.6 produces output,
  GPT-5.5 reviews before the reply is sent.
- Add review result to event log.
- Add iteration logic: up to 2 re-runs if review requests changes.
- Queue for human if iterations exhausted.
- Tests: review approve, review reject, iteration cap, human queue.

### Milestone 5: GitHub PR bridge (Post-v1.3)

- Add `github` subcommand group.
- Implement `create-branch`, `relay-review`, `sync-status`.
- Add `github_pr` action type in dispatcher.
- Implement PR creation via `gh` CLI.
- Implement review relay (GitHub → A2A note).
- Tests: branch creation, PR metadata parsing, review relay.

### Milestone 6: Integration and staged rollout (Post-v1.3 for GitHub/write flows)

- End-to-end test: request → read-only/draft agent output → review/redaction
  gate → private reply.
- Post-v1.3 only: end-to-end test for request → branch → PR → review → merge
  → reply.
- Staged promotion: manual → receipt → ack_only → echo_dispatch →
  constrained_agent for each agent pair.
- `doctor` enhancements for v1.3 health checks.
- Documentation update.

---

## 10. Acceptance Tests

### Unit tests (`tests/test_v13_policy.py`)

1. Old `dispatcher.json` without new fields loads with safe defaults.
2. `safety_level` blocks dispatch when level is below `echo_dispatch`.
3. `max_replies_per_thread` stops dispatch at threshold.
4. `max_auto_threads_per_hour` stops dispatch at threshold.
5. `cooldown_seconds` blocks dispatch within cooldown window.
6. `blocked_keywords_in_body` queues matching messages for human.
7. `require_thread_id` rejects messages without thread_id.
8. Global rate limits block dispatch at ceiling.
9. `--dry-run` produces correct decision without side effects.
10. Safety level promotion validates acceptance criteria.
11. Safety level demotion is immediate and unconditional.

### Unit tests (`tests/test_v13_agent_runner.py`)

12. `agent_runner` action type invokes cursor-agent subprocess.
13. `sandbox: true` prevents write tool calls.
14. `max_tool_calls` enforcement stops agent at limit.
15. Model unavailable falls back to `argv_fallback`.
16. Both unavailable logs `dispatch_failed`.
17. Timeout triggers clean failure.
18. Review pass approves → reply sent.
19. Review pass rejects → iteration → re-run.
20. Review iterations exhausted → queued for human.

### Integration tests (`tests/test_v13_e2e.py`)

21. Full pair: alpha sends request → bravo dispatches → reply arrives.
22. Cross-pair message: alpha → delta → queued for human (not auto-dispatched).
23. Thread with 6 replies → 6th dispatch blocked.
24. Rapid-fire 15 requests in 1 minute → rate limit triggers.
25. Keyword `"DROP TABLE"` in body → queued for human.
26. Post-v1.3 only: agent runner produces a branch and PR → review → approved
    → merged.

### Smoke tests (CI, `.github/workflows/ci.yml`)

27. `dispatch --dry-run` exits 0 with JSON output.
28. `safety-level show` exits 0.
29. Schema migration from v1 config produces valid v2 config.

---

## 11. Failure Modes and Mitigations

| Failure mode                        | Detection                                  | Mitigation                                        |
|-------------------------------------|--------------------------------------------|----------------------------------------------------|
| Auto-reply loop                     | Thread reply count exceeds cap             | `max_replies_per_thread` blocks; event logged      |
| Agent runner hangs                  | Timeout fires                              | Process killed; `dispatch_failed` logged           |
| Agent runner produces harmful output| Review pass catches; keyword blocklist     | Review rejects; message queued for human           |
| Model API rate limit                | HTTP 429 / API error                       | Fallback to `argv_fallback`; backoff               |
| Model API outage                    | Connection error                           | Fallback; if both fail, queue for human            |
| Dispatcher crash mid-dispatch       | Message left in `processing/`              | `--recover-processing` on restart                  |
| Duplicate dispatch                  | Seen-state check                           | Dedupe by id/idempotency_key (existing)            |
| GitHub API failure                  | `gh` exit code non-zero                    | Retry once; log failure; do not send reply         |
| Disk full                           | Write failure                              | Dispatcher stops; `doctor` reports                 |
| Config corruption                   | JSON parse error                           | Dispatcher refuses to start; logs error            |
| Cross-pair message auto-dispatch    | Sender not in `allowed_from`               | Policy gate blocks; existing behavior              |
| Secret in message body              | Keyword blocklist or review pass           | Queue for human; never auto-reply                  |
| Safety level bypassed               | Level check in policy gate                 | Hard check; not configurable per-message           |

---

## 12. Rollback Plan

### Per-agent rollback

Demote any agent to `receipt` or `manual` immediately:

```bash
a2a-relay --base $BASE safety-level demote \
  --agent bravo@example \
  --to receipt \
  --reason "unexpected dispatch behavior"
```

This is instant: the next dispatch cycle respects the new level. No restart
required; the watcher reads the level from `dispatcher.json` on every cycle.

### Full rollback to v0.3 behavior

1. Remove new fields from `dispatcher.json` (or restore from backup).
2. The dispatcher ignores unknown fields; v0.3 behavior resumes.
3. Agent runner actions without `type` field default to `subprocess`.
4. `review` section, if absent, disables review.
5. Rate limits, if absent, default to unlimited (v0.3 behavior).

### Emergency stop

```bash
# Disable all dispatch for an agent
a2a-relay --base $BASE safety-level demote \
  --agent bravo@example \
  --to manual \
  --reason "emergency stop"

# Or disable in config
# Set "enabled": false in dispatcher.json for the agent

# Kill running watcher
systemctl stop a2a-watch@bravo_example.service
```

### Data safety

- No schema migration is destructive. New fields have defaults.
- Event log entries from v1.3 are additive; they don't break v0.3 readers.
- Messages in `inbox/`, `processing/`, `archive/` are unchanged format.

---

## 13. Public/Private Boundary Rules

### Public repo contains

- Generic `@example` agent IDs in all docs, examples, and tests.
- `dispatcher.json` examples with placeholder paths (`/srv/agent-workspace`).
- Design docs referencing abstract agent pairs (alpha/bravo/charlie/delta).
- No hostnames, IP addresses, tunnel ports, or real user names.
- No API keys, tokens, or credential references.

### `local/` directory contains (gitignored)

- Real agent ID mapping (e.g., which `@example` maps to which real agent).
- Real mailbox base paths.
- Real hostnames and deployment topology.
- Runbook with actual commands for private agents.
- Rollout notes with dates and real incident references.

### Enforcement

- `.gitignore` includes `local/`.
- CI check: `grep -r` for known private patterns in committed files (added
  to `.github/workflows/ci.yml`).
- Design docs include a header reminder: *"Use generic agent names. Real
  deployment details go in `local/`."*

---

## 14. Event Types Added in v1.3

The non-GitHub event types are v1.3 candidates. GitHub events are reserved names
for post-v1.3 work unless the implementation is metadata-only and cannot write
to GitHub without human approval.

| Event type                    | When                                              |
|-------------------------------|---------------------------------------------------|
| `safety_level_changed`        | Agent safety level promoted or demoted             |
| `dispatch_rate_limited`       | Dispatch blocked by rate limit                     |
| `dispatch_cooldown`           | Dispatch blocked by cooldown timer                 |
| `dispatch_thread_cap`         | Dispatch blocked by thread reply cap               |
| `dispatch_keyword_blocked`    | Dispatch blocked by keyword blocklist              |
| `agent_runner_started`        | Agent runner invoked (model, profile logged)       |
| `agent_runner_succeeded`      | Agent runner completed successfully                |
| `agent_runner_failed`         | Agent runner failed (timeout, error, sandbox)      |
| `agent_runner_fallback`       | Fell back to argv_fallback subprocess              |
| `review_started`              | Review pass invoked                                |
| `review_approved`             | Review pass approved the reply                     |
| `review_changes_requested`    | Review pass requested changes                      |
| `review_iteration`            | Implementation re-run after review feedback        |
| `review_exhausted`            | Max review iterations reached; queued for human    |
| `github_branch_created`       | Feature branch created for A2A thread              |
| `github_pr_created`           | PR opened                                          |
| `github_pr_review_posted`     | Review posted on PR                                |
| `github_pr_merged`            | PR merged                                          |
| `github_pr_failed`            | GitHub operation failed                            |

---

## 15. Migration Path

### From v0.3 to v1.3

1. **Config**: existing `dispatcher.json` continues to work. New fields
   default to safe values (`safety_level: "manual"`, no rate limits, no
   review).
2. **Events**: new event types are additive. Old event readers skip unknown
   types.
3. **CLI**: new subcommands (`safety-level`, `github`) do not conflict with
   existing commands.
4. **Tests**: existing `test_dispatcher.py` and `test_receipt.py` must pass
   without modification.
5. **Rollout**: each agent pair starts at `manual` and promotes through
   levels independently. No big-bang migration.

### Deprecations

- `argv` in actions remains supported but `type: "subprocess"` should be
  explicit for clarity.
- `safe-echo` action is renamed convention only; old name still works.

---

## 16. Open Questions

1. **Thread history in agent context**: should the agent runner receive
   prior message bodies from the same thread, or only metadata? Body
   inclusion increases context quality but also increases risk of prompt
   injection chains. Current proposal: metadata only, with opt-in
   `include_thread_bodies: true` requiring Level 5.

2. **Review model selection**: is GPT-5.5 the right choice for review, or
   should the same model (Opus 4.6) review its own output with a different
   system prompt? Cross-model review is more robust but adds latency and
   cost.

3. **GitHub token management**: the `gh` CLI needs authentication. Should
   the dispatcher manage this, or should the operator configure `gh auth`
   separately? Current proposal: operator configures `gh auth` separately;
   the dispatcher only invokes `gh` and checks exit codes.

4. **Multi-mailbox orchestration**: if alpha and bravo are on different
   mailbox hosts, does the orchestrator need a federation layer? Current
   proposal: defer federation; v1.3 assumes a single shared mailbox host.

---

## 17. Relationship to Existing Design Docs

- **`2026-05-08-a2a-relay-upgrade-plan.md`**: v1.3 builds on the v0.3
  dispatcher and extends toward the v0.4/v0.5 goals. The safety level
  system is the bridge between local-only dispatch and webhook-ready trust.

- **`2026-05-09-runtime-watchers-and-multi-contact.md`**: the staged safety
  levels (§7) directly implement Levels 0–3 from that doc and extend to
  Levels 4–5 with agent runner integration.

- **`2026-05-09-task-delegation-worker.md`**: the worker profiles
  (`read-only-fast`, `ops-review`, `approval-required`) are preserved.
  `task send` remains the recommended CLI for structured handoffs.

- **`2026-05-08-v03-dispatcher-development-path.md`**: the policy gate
  invariants from v0.3 are preserved. v1.3 adds rate limits, keyword
  blocklist, and safety level as additional gate conditions.

---

*Use generic agent names in public docs. Real deployment details belong in
`local/`.*
