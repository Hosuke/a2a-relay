# v1.3 Implementation Slices

Date: 2026-05-09
Status: planning
Basis: `2026-05-09-v13-auto-reply-and-git-collaboration.md`, `2026-05-09-v13-gpt55-review.md`

---

## Ordering Rationale

Slices are ordered by safety: each PR is merge-ready independently and cannot
introduce runaway behavior. Later slices depend on earlier ones but earlier
slices never depend on later ones.

1. Protocol fields and no-loop tests (pure additive schema, zero behavior change)
2. Policy decision events (audit only, no dispatch change)
3. Failure status semantics (dispatch failure becomes sender-visible)
4. Constrained auto-reply runner (the core automation, guarded by all prior work)
5. GitHub interaction docs only (no code, just docs/config schema)
6. Optional runner integration (cursor-agent backend, behind feature flag)

---

## Slice 1: Protocol Fields and No-Loop Tests

**Goal**: Add `auto_generated`, `hop_count`, `max_hops` fields to the message
schema. Add validation and dispatch-gate enforcement. Prove no-loop invariants
with new tests. Zero behavior change for existing messages that omit new fields.

### Files touched

| File | Change |
|------|--------|
| `a2a_relay/core.py` | Add `auto_generated: bool`, `hop_count: int`, `max_hops: int` to `A2AMessage` dataclass. Update `validate_message` to reject `hop_count >= max_hops` when both present. Update `make_message` to accept and propagate new fields. |
| `a2a_relay/dispatcher.py` | In `dispatch_message`, set `auto_generated=True` and `needs_reply=False` on generated replies. In `check_policy_gate`, reject messages where `auto_generated=True` unless policy explicitly allows (default: reject). Increment `hop_count` on outbound replies. |
| `docs/message-schema.md` | Document new optional fields: `auto_generated`, `hop_count`, `max_hops`. |
| `docs/protocol.md` | Add no-loop invariant section. |
| `tests/test_no_loop.py` | **New file.** Tests below. |
| `tests/test_dispatcher.py` | Add test that dispatcher-generated reply has `auto_generated=True` and `needs_reply=False`. |

### Tests (`tests/test_no_loop.py`)

1. `test_auto_generated_reply_not_dispatched` — auto_generated=true request → dispatch returns ineligible.
2. `test_hop_count_at_max_rejected` — hop_count=3, max_hops=3 → validation rejects.
3. `test_hop_count_below_max_accepted` — hop_count=1, max_hops=3 → passes.
4. `test_dispatcher_reply_sets_auto_generated_true` — dispatch a valid request, inspect reply JSON on disk.
5. `test_dispatcher_reply_sets_needs_reply_false` — same reply, `needs_reply` must be false.
6. `test_dispatcher_increments_hop_count` — request with hop_count=0 → reply has hop_count=1.
7. `test_missing_hop_count_defaults_zero` — backward compat: no hop_count field → treated as 0.
8. `test_status_ack_does_not_trigger_dispatch` — status message stays ineligible (existing invariant, new coverage).
9. `test_reply_in_inbox_does_not_dispatch` — reply placed directly in inbox, dispatch skips it.
10. `test_duplicate_action_pair_blocked` — same (message_id, action) cannot run twice even across restarts.

### Reviewer prompt

> Verify: (a) existing tests pass without modification, (b) messages without
> new fields still validate, (c) `auto_generated` defaults to false/absent,
> (d) no behavior changes for v0.3-shaped configs, (e) hop_count increment
> is only on dispatcher-generated outbound, not on relay/forward.

---

## Slice 2: Policy Decision Events

**Goal**: Emit a structured `policy_decision` event for every claimed message,
recording the exact gate inputs and outcome. Metadata-only (no body content in
events). Enables auditors to reconstruct why any message was or was not dispatched.

### Files touched

| File | Change |
|------|--------|
| `a2a_relay/dispatcher.py` | Add `_log_policy_decision(base, msg, agent_id, decision, reason, action, ...)` helper. Call it at every exit point of `check_policy_gate` and in `dispatch_message` after action selection. Include `hop_count`, `auto_generated`, `body_sha256` (hashlib on body). |
| `a2a_relay/core.py` | Add `body_sha256(body: str) -> str` utility (sha256 hex of utf-8 body). |
| `tests/test_policy_events.py` | **New file.** Tests below. |

### Tests (`tests/test_policy_events.py`)

1. `test_eligible_message_emits_policy_decision_dispatch` — event with decision=dispatch present in JSONL.
2. `test_rejected_message_emits_policy_decision_reject` — unknown sender → decision=reject.
3. `test_queued_message_emits_policy_decision_queue` — human_approval → decision=queue.
4. `test_policy_decision_contains_body_sha256` — sha256 matches computed hash.
5. `test_policy_decision_does_not_contain_body_text` — raw body string absent from event JSONL.
6. `test_policy_decision_records_hop_count` — hop_count value present in event.
7. `test_policy_decision_records_action_name` — action field present when dispatch.
8. `test_every_claimed_message_has_exactly_one_policy_decision` — dispatch two messages, count events.

### Reviewer prompt

> Verify: (a) event JSONL never contains message body text, (b) sha256 is
> computed identically for the same body across calls, (c) events are written
> even when dispatch is skipped/rejected, (d) no performance regression from
> hashing (body_sha256 only called once per message).

---

## Slice 3: Failure Status Semantics

**Goal**: When dispatch fails (timeout, nonzero exit, OS error), optionally send
a metadata-only failure `status` message back to the sender. Controlled by a new
policy field `send_failure_status: bool` (default false for backward compat).

### Files touched

| File | Change |
|------|--------|
| `a2a_relay/dispatcher.py` | After `run_action` failure, if `policy.get("send_failure_status")`, construct and send a `status` message with `auto_generated=True`, `needs_reply=False`, subject "Dispatch failed", body containing only error category (not stderr/stdout). Log `dispatch_failure_status_sent` event. |
| `a2a_relay/core.py` | No change (make_message already supports type=status). |
| `docs/message-schema.md` | Document failure status convention. |
| `tests/test_failure_status.py` | **New file.** Tests below. |
| `tests/test_dispatcher.py` | Add test confirming existing behavior unchanged when `send_failure_status` absent. |

### Tests (`tests/test_failure_status.py`)

1. `test_failure_status_sent_on_timeout` — timeout → status message in sender inbox.
2. `test_failure_status_sent_on_nonzero_exit` — exit 1 → status message arrives.
3. `test_failure_status_not_sent_when_disabled` — policy without field → no status sent (backward compat).
4. `test_failure_status_is_auto_generated` — status msg has `auto_generated=True`.
5. `test_failure_status_has_needs_reply_false` — cannot trigger reply loop.
6. `test_failure_status_body_has_no_stderr` — stderr content absent from status body.
7. `test_failure_status_body_has_no_stdout` — partial stdout absent from status body.
8. `test_failure_status_event_logged` — `dispatch_failure_status_sent` event in JSONL.

### Reviewer prompt

> Verify: (a) failure status body reveals only the error category string, never
> process output, (b) `auto_generated=True` and `needs_reply=False` are set,
> (c) backward compat: no status sent when field absent, (d) failure status
> itself cannot trigger a dispatch (covered by Slice 1 no-loop invariant).

---

## Slice 4: Constrained Auto-Reply Runner

**Goal**: Add rate limiting, cooldown, thread reply cap, keyword blocklist, and
`require_thread_id` to the policy gate. This is the core safety machinery for
bounded auto-reply. No agent_runner yet — dispatch still uses subprocess actions.

### Files touched

| File | Change |
|------|--------|
| `a2a_relay/dispatcher.py` | Extend `check_policy_gate` with: (1) `require_thread_id` check, (2) `blocked_keywords_in_body` scan, (3) `max_replies_per_thread` — count `dispatch_reply_sent` events for thread, (4) `cooldown_seconds` — check last `dispatch_started` timestamp, (5) `max_auto_threads_per_hour` — sliding window over events, (6) global `rate_limits.global_max_dispatches_per_hour`. Add `--dry-run` flag support returning decision JSON without side effects. |
| `a2a_relay/cli.py` | Add `--dry-run` argument to `dispatch` subcommand. When set, call `check_policy_gate` and print decision JSON without running action. |
| `a2a_relay/core.py` | Add `count_events(base, event_type, thread_id=None, since=None) -> int` helper for rate/cap checks. |
| `tests/test_v13_policy.py` | **New file.** Tests below. |

### Tests (`tests/test_v13_policy.py`)

1. `test_thread_reply_cap_blocks_at_threshold` — 5 prior `dispatch_reply_sent` events → 6th blocked.
2. `test_thread_reply_cap_allows_below_threshold` — 2 prior events → dispatch eligible.
3. `test_cooldown_blocks_within_window` — `dispatch_started` 10s ago, cooldown=30 → blocked.
4. `test_cooldown_allows_after_window` — `dispatch_started` 60s ago, cooldown=30 → eligible.
5. `test_hourly_thread_cap_blocks` — 10 unique thread dispatches in hour, cap=10 → 11th blocked.
6. `test_keyword_blocklist_matches` — body contains "DROP TABLE" → queued for human.
7. `test_keyword_blocklist_case_insensitive` — "drop table" matches "DROP TABLE" entry.
8. `test_keyword_blocklist_no_match_passes` — clean body → eligible.
9. `test_require_thread_id_rejects_missing` — no thread_id, require_thread_id=true → rejected.
10. `test_require_thread_id_accepts_present` — thread_id present → eligible.
11. `test_global_rate_limit_blocks` — 60 dispatches in hour, global cap=60 → 61st blocked.
12. `test_dry_run_returns_decision_no_side_effects` — --dry-run produces JSON, no events written, no action run.
13. `test_old_config_without_new_fields_still_works` — v0.3-shaped config dispatches normally.

### Reviewer prompt

> Verify: (a) all new checks are in `check_policy_gate` before action invocation,
> (b) event counting queries are bounded in time (no full-scan of all history),
> (c) `--dry-run` is truly side-effect-free (no events, no seen marks, no
> subprocess), (d) old configs without new fields pass with safe defaults,
> (e) keyword match does not leak matched term into reply or event body.

---

## Slice 5: GitHub Interaction Docs Only

**Goal**: Document the future GitHub PR bridge design without implementing code.
Add schema examples for `github_pr` action type. Add placeholder CLI help text.
No functional code. Establishes the contract for Slice 6 and future PRs.

### Files touched

| File | Change |
|------|--------|
| `docs/design/2026-05-09-v13-github-bridge.md` | **New file.** Extracted and refined from §5 of the main design doc. Includes: action schema, workflow diagram, CLI grammar, security requirements, trust boundaries, and what is NOT in v1.3. |
| `docs/configuration.md` | Add section documenting `github_pr` action type schema (reserved, not yet functional). |
| `a2a_relay/cli.py` | Add `github` subcommand group with stub parsers for `create-branch`, `relay-review`, `sync-status`. Each prints "not yet implemented" and exits 0. |
| `tests/test_cli_v02.py` | Add test that `github create-branch --help` exits 0. |

### Tests

1. `test_github_subcommand_help_exits_zero` — `a2a-relay github create-branch --help` → exit 0.
2. `test_github_stub_prints_not_implemented` — `a2a-relay github create-branch ...` → "not yet implemented".
3. `test_github_pr_action_schema_in_docs` — verify docs/configuration.md contains `github_pr` section (manual/CI grep check).

### Reviewer prompt

> Verify: (a) no functional GitHub integration code lands, (b) stub commands
> are clearly marked not-implemented, (c) docs explicitly state what is NOT
> in v1.3, (d) security requirements section covers: webhook signatures,
> actor allowlists, no auto-posting, untrusted PR text handling.

---

## Slice 6: Optional Agent Runner Integration

**Goal**: Add `agent_runner` action type support in the dispatcher, behind a
feature check (action `type` field must be `"agent_runner"`). Implement the
cursor-agent subprocess backend with sandbox, timeout, max_tool_calls via CLI
arguments. Include model fallback to `argv_fallback`. This slice is optional
and can be deferred if Slices 1–4 need more stabilization.

### Files touched

| File | Change |
|------|--------|
| `a2a_relay/dispatcher.py` | Add `run_agent_runner(action_config, stdin_prompt, *, stdout_max_chars) -> tuple[bool, str, str]` function. Dispatches to cursor-agent CLI subprocess with `--sandbox`, `--max-tool-calls`, `--model`, `--timeout` flags. On failure, falls back to `argv_fallback` if configured. Update `dispatch_message` to check `action_config.get("type") == "agent_runner"` and route to new function. |
| `a2a_relay/core.py` | No change. |
| `a2a_relay/cli.py` | No change (dispatch already routes through `dispatch_message`). |
| `tests/test_agent_runner.py` | **New file.** Tests below. |
| `docs/configuration.md` | Document `agent_runner` action type fields: `runner`, `model`, `profile`, `sandbox`, `timeout_seconds`, `max_tool_calls`, `argv_fallback`. |

### Tests (`tests/test_agent_runner.py`)

1. `test_agent_runner_invokes_subprocess` — mock cursor-agent CLI, verify invocation args.
2. `test_agent_runner_sandbox_flag_passed` — sandbox=true → `--sandbox` in argv.
3. `test_agent_runner_timeout_kills_process` — slow mock → timeout → failure result.
4. `test_agent_runner_max_tool_calls_passed` — verify `--max-tool-calls 20` in argv.
5. `test_agent_runner_fallback_on_failure` — cursor-agent fails → argv_fallback runs.
6. `test_agent_runner_both_fail_logs_dispatch_failed` — both fail → dispatch_failed event.
7. `test_agent_runner_stdout_truncated` — long output → truncated to stdout_max_chars.
8. `test_agent_runner_stderr_not_in_reply` — stderr from runner not included in reply body.
9. `test_subprocess_action_still_works` — existing subprocess actions unaffected by new code path.
10. `test_missing_runner_field_defaults_to_subprocess` — action without `type` → old path.

### Reviewer prompt

> Verify: (a) `shell=False` is used for all subprocess invocations, (b) model
> name is passed as a CLI flag not interpolated into a shell string, (c) fallback
> is silent to the sender (same reply shape), (d) event log records which
> backend ran (cursor-agent vs fallback), (e) existing subprocess tests pass
> without modification, (f) sandbox flag is never skipped even if config is
> malformed.

---

## Dependency Graph

```
Slice 1 (protocol fields)
    │
    ├── Slice 2 (policy decision events) — uses hop_count, auto_generated
    │
    ├── Slice 3 (failure status) — uses auto_generated, needs_reply=false
    │
    └── Slice 4 (constrained runner) — uses all prior invariants
            │
            ├── Slice 5 (GitHub docs) — independent, can merge any time after 1
            │
            └── Slice 6 (agent runner) — optional, depends on 4 for rate limits
```

---

## Estimated Sizes

| Slice | New lines (approx) | Test lines (approx) | Risk |
|-------|--------------------:|---------------------:|------|
| 1     | ~80                 | ~150                 | Low — additive schema, zero behavior change |
| 2     | ~60                 | ~120                 | Low — event logging only |
| 3     | ~50                 | ~100                 | Low — opt-in, default off |
| 4     | ~150                | ~200                 | Medium — core safety gate changes |
| 5     | ~20 code + docs     | ~30                  | Minimal — docs and stubs |
| 6     | ~120                | ~160                 | Medium — new execution path |

---

## Review Cadence

- Slices 1–3: can be reviewed and merged in parallel after Slice 1 lands.
- Slice 4: requires sign-off from both design leads (safety-critical gate logic).
- Slice 5: can merge any time after Slice 1 (no code dependency).
- Slice 6: should wait for Slice 4 to stabilize (48h soak recommended).

---

*Use generic agent names in public docs. Real deployment details belong in `local/`.*
