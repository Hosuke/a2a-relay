"""v0.3 policy-gated private auto-reply dispatcher.

Local-only dispatcher that runs pre-registered actions via subprocess when
incoming messages pass the policy gate. Message body never chooses command.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .core import (
    ContactError,
    RelayConfig,
    ValidationError,
    validate_message,
    has_seen,
    load_contacts,
    log_event,
    make_message,
    mark_seen,
    resolve_contact,
    send_message,
)

DEFAULT_STDOUT_MAX_CHARS = 12000
DEFAULT_TIMEOUT_SECONDS = 120


def load_dispatcher_config(base: Path) -> dict:
    path = base / "dispatcher.json"
    if not path.exists():
        return {"agents": {}, "actions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def get_agent_policy(config: dict, agent_id: str) -> dict | None:
    return config.get("agents", {}).get(agent_id)


def get_action(config: dict, name: str) -> dict | None:
    return config.get("actions", {}).get(name)


def check_policy_gate(base: Path, msg: dict, agent_id: str, config: dict) -> tuple[bool, str]:
    """Return (eligible, reason). If not eligible, reason explains why."""
    try:
        validate_message(msg, config=RelayConfig())
    except ValidationError as exc:
        return False, str(exc)
    if msg.get("to") != agent_id:
        return False, f"target mismatch: {msg.get('to')} != {agent_id}"

    policy = get_agent_policy(config, agent_id)
    if policy is None:
        return False, "no_dispatcher_policy_for_agent"
    if not policy.get("enabled", False):
        return False, "dispatcher_disabled_for_agent"

    msg_type = msg.get("type", "")
    if msg_type in ("reply", "status", "heartbeat"):
        return False, f"type_{msg_type}_never_dispatched"
    if msg_type != "request":
        return False, f"type_{msg_type}_not_request"

    if not msg.get("needs_reply", False):
        return False, "needs_reply_is_false"

    if msg.get("human_approval_required", False):
        return False, "human_approval_required"

    sender = msg.get("from", "")
    allowed_from = policy.get("allowed_from", [])
    if not isinstance(allowed_from, list) or not allowed_from:
        return False, "allowed_from_required"

    contacts_data = load_contacts(base)
    try:
        resolved_sender = resolve_contact(contacts_data, sender)
    except ContactError:
        return False, "sender_unknown_contact"

    allowed_resolved = set()
    for allowed in allowed_from:
        if not isinstance(allowed, str) or not allowed:
            return False, "invalid_allowed_from_config"
        resolved_allowed = allowed
        try:
            resolved_allowed = resolve_contact(contacts_data, allowed)
        except ContactError:
            pass
        allowed_resolved.add(resolved_allowed)
    if resolved_sender not in allowed_resolved:
        return False, "sender_not_in_allowed_from"

    if has_seen(base, msg):
        return False, "duplicate_message"

    return True, "eligible"


def build_stdin_prompt(msg: dict) -> str:
    """Build delimited stdin prompt. Do not log full prompt."""
    sender = msg.get("from", "")
    subject = msg.get("subject", "")
    body = msg.get("body", "")
    return (
        "<a2a_message>\n"
        f"sender: {sender}\n"
        f"subject: {subject}\n"
        f"body: {body}\n"
        "</a2a_message>\n"
    )


def run_action(action_config: dict, stdin_prompt: str, *, stdout_max_chars: int = DEFAULT_STDOUT_MAX_CHARS) -> tuple[bool, str, str]:
    """Run pre-registered action. Returns (success, stdout_truncated, error_summary)."""
    argv = action_config.get("argv")
    if not isinstance(argv, list) or not argv:
        return False, "", "invalid_argv_config"
    if not all(isinstance(item, (str, Path)) for item in argv):
        return False, "", "invalid_argv_config"

    timeout = action_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    cwd = action_config.get("cwd")
    try:
        timeout = float(timeout)
        if timeout <= 0:
            return False, "", "invalid_timeout_config"
    except (TypeError, ValueError):
        return False, "", "invalid_timeout_config"

    try:
        result = subprocess.run(
            argv,
            shell=False,
            timeout=timeout,
            cwd=cwd,
            text=True,
            capture_output=True,
            input=stdin_prompt,
        )
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except (OSError, TypeError, ValueError) as exc:
        return False, "", f"os_error: {exc}"

    if result.returncode != 0:
        return False, "", f"nonzero_exit_{result.returncode}"

    stdout = result.stdout or ""
    if len(stdout) > stdout_max_chars:
        stdout = stdout[:stdout_max_chars]

    return True, stdout, ""


def dispatch_message(base: Path, msg: dict, agent_id: str, action_name: str | None = None,
                     *, ack: bool = False) -> dict[str, Any]:
    """Full dispatch pipeline for one message. Returns result dict."""
    config = load_dispatcher_config(base)
    policy = get_agent_policy(config, agent_id)

    eligible, reason = check_policy_gate(base, msg, agent_id, config)

    if not eligible:
        if reason == "human_approval_required":
            log_event(base, "dispatch_queued_for_human", message=msg, actor=agent_id, reason=reason)
            return {"dispatched": False, "reason": reason, "queued_for_human": True}
        log_event(base, "dispatch_skipped", message=msg, actor=agent_id, reason=reason)
        return {"dispatched": False, "reason": reason}

    log_event(base, "dispatch_eligible", message=msg, actor=agent_id)

    resolved_action = action_name or (policy or {}).get("default_action")
    if not resolved_action:
        log_event(base, "dispatch_skipped", message=msg, actor=agent_id, reason="no_action_configured")
        return {"dispatched": False, "reason": "no_action_configured"}

    action_config = get_action(config, resolved_action)
    if not action_config:
        log_event(base, "dispatch_skipped", message=msg, actor=agent_id, reason=f"action_not_found:{resolved_action}")
        return {"dispatched": False, "reason": f"action_not_found:{resolved_action}"}

    stdout_max = (policy or {}).get("stdout_max_chars", DEFAULT_STDOUT_MAX_CHARS)
    stdin_prompt = build_stdin_prompt(msg)

    log_event(base, "dispatch_started", message=msg, actor=agent_id,
              extra={"action": resolved_action})

    success, stdout, error = run_action(action_config, stdin_prompt, stdout_max_chars=stdout_max)

    if not success:
        log_event(base, "dispatch_failed", message=msg, actor=agent_id,
                  reason=error, extra={"action": resolved_action})
        return {"dispatched": True, "success": False, "reason": error}

    log_event(base, "dispatch_succeeded", message=msg, actor=agent_id,
              extra={"action": resolved_action})

    mark_seen(base, msg, actor=agent_id)

    reply_body = stdout.strip() if stdout.strip() else "(empty reply)"
    reply_msg = make_message(
        agent_id, msg["from"], "reply",
        f"Re: {msg.get('subject', '')}",
        reply_body,
        reply_to=msg.get("id"),
        thread_id=msg.get("thread_id"),
    )
    reply_path = send_message(base, reply_msg)
    log_event(base, "dispatch_reply_sent", message=msg, actor=agent_id,
              extra={"reply_path": str(reply_path), "action": resolved_action})

    return {"dispatched": True, "success": True, "reply_path": str(reply_path)}
