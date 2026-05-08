from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import (
    Contact,
    ContactError,
    RelayConfig,
    ValidationError,
    add_contact,
    archive_message,
    claim_message,
    has_seen,
    init_mailbox,
    list_messages,
    load_contacts,
    processing_dir,
    log_event,
    make_message,
    mark_seen,
    read_message,
    remove_contact,
    resolve_agent,
    resolve_contact,
    safe_name,
    send_message,
    validate_message,
)
from .dispatcher import dispatch_message

RECEIPT_ARCHIVE_TYPES = {"note", "status", "reply", "heartbeat"}
RECEIPT_DEFAULT_TYPES = sorted(RECEIPT_ARCHIVE_TYPES | {"request"})
FAILED_EVENT_TYPES = {"failed", "dispatch_failed"}
TIMELINE_FIELDS = ("timestamp", "event_type", "actor", "message_id", "from", "to", "reason", "path")


def cmd_init(args):
    init_mailbox(Path(args.base), args.agent)
    print(Path(args.base).resolve())


def cmd_send(args):
    base = Path(args.base)
    recipient = resolve_agent(base, args.to)
    sender = resolve_agent(base, args.from_)
    msg = make_message(
        sender, recipient, args.type, args.subject, args.body,
        reply_to=args.reply_to, thread_id=args.thread_id,
        urgency=args.urgency, needs_reply=args.needs_reply,
        idempotency_key=args.idempotency_key,
    )
    path = send_message(base, msg)
    print(path)


def cmd_reply(args):
    base = Path(args.base)
    sender = resolve_agent(base, args.from_)
    if args.message_file:
        original = read_message(Path(args.message_file))
        to = args.to or original.get("from")
        reply_to = args.reply_to or original.get("id")
        thread_id = args.thread_id or original.get("thread_id")
        subject = args.subject or f"Re: {original.get('subject', '')}"
    else:
        to = args.to
        reply_to = args.reply_to
        thread_id = args.thread_id
        subject = args.subject
    if not to or not reply_to or not thread_id:
        raise SystemExit("reply requires --to, --reply-to and --thread-id, or --message-file")
    recipient = resolve_agent(base, to)
    msg = make_message(
        sender, recipient, "reply", subject or "Re", args.body,
        reply_to=reply_to, thread_id=thread_id,
        urgency=args.urgency,
    )
    path = send_message(base, msg)
    log_event(base, "replied", message=msg.to_json_dict(), actor=sender, extra={"reply_to": reply_to})
    print(path)


def _build_task_body(args) -> str:
    constraints = args.constraint or [
        "Do not make destructive changes.",
        "Do not restart services or modify configuration unless explicitly approved.",
        "Do not expose secrets in the reply.",
        "If write/restart/delete/migration is needed, reply with human_approval_required.",
    ]
    outputs = args.require_output or [
        "summary",
        "commands run",
        "evidence",
        "suspected cause",
        "recommended next action",
        "whether human approval is needed",
    ]
    lines = [
        "# Task",
        "",
        args.title,
        "",
        "## Context",
        "",
    ]
    if args.context:
        for item in args.context:
            lines.append(f"- {item}")
    else:
        lines.append("- No additional context provided.")
    lines.extend([
        "",
        "## Constraints",
        "",
        f"- profile: {args.profile}",
        f"- human_approval_required: {str(args.approval_required).lower()}",
    ])
    for item in constraints:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## Required output",
        "",
    ])
    for idx, item in enumerate(outputs, start=1):
        lines.append(f"{idx}. {item}")
    lines.append("")
    return "\n".join(lines)


def cmd_task_send(args):
    base = Path(args.base)
    sender = resolve_agent(base, args.from_)
    recipient = resolve_agent(base, args.to)
    thread_id = args.thread_id or f"task_{safe_name(sender)}_to_{safe_name(recipient)}_{safe_name(args.title)[:40]}"
    msg = make_message(
        sender,
        recipient,
        "request",
        args.title,
        _build_task_body(args),
        thread_id=thread_id,
        urgency=args.urgency,
        needs_reply=True,
        idempotency_key=args.idempotency_key,
    )
    msg.capabilities_requested = args.capability or []
    msg.human_approval_required = bool(args.approval_required)
    path = send_message(base, msg)
    print(path)


def _config_from_args(args) -> RelayConfig:
    base = Path(args.base)
    allow_from = {resolve_agent(base, item) for item in (args.allow_from or [])}
    return RelayConfig(
        allow_from=allow_from,
        allowed_types=set(args.allowed_type or []),
        max_body_chars=args.max_body_chars,
        trusted_filesystem_unsigned=True,
    )


def _handle_claimed_message(args, base: Path, agent: str, path: Path, *, dispatch_action: str | None = None) -> dict:
    config = _config_from_args(args)
    msg = None
    try:
        msg = read_message(path)
        validate_message(msg, config=config)
        if msg.get("to") != agent:
            raise ValidationError(f"target mismatch: {msg.get('to')} != {agent}")
        if has_seen(base, msg):
            archive = archive_message(base, path, ok=True, message_id=msg.get("id"))
            log_event(base, "duplicate", message=msg, actor=agent, path=str(archive))
            return {"path": str(path), "ok": True, "duplicate": True, "archive": str(archive), "message_id": msg.get("id"), "from": msg.get("from"), "to": msg.get("to"), "type": msg.get("type"), "subject": msg.get("subject"), "thread_id": msg.get("thread_id")}
        log_event(base, "received", message=msg, actor=agent, path=str(path))
        ack_path = None
        if args.ack:
            ack = make_message(
                agent, msg["from"], "status", f"ACK: {msg.get('subject','')}",
                f"{agent} received: {msg.get('subject','')}",
                reply_to=msg.get("id"), thread_id=msg.get("thread_id"),
            )
            ack_path = send_message(base, ack)
            log_event(base, "acked", message=msg, actor=agent, extra={"ack_path": str(ack_path)})
        result = {"path": str(path), "ok": True, "ack": str(ack_path) if ack_path else None, "message_id": msg.get("id"), "from": msg.get("from"), "to": msg.get("to"), "type": msg.get("type"), "subject": msg.get("subject"), "thread_id": msg.get("thread_id")}
        if dispatch_action:
            dispatch = dispatch_message(base, msg, agent, None if dispatch_action == "__default__" else dispatch_action, ack=args.ack)
            result["dispatch"] = dispatch
            if dispatch.get("queued_for_human"):
                result["queued_for_human"] = True
                return result
        else:
            mark_seen(base, msg, actor=agent)
        archive = archive_message(base, path, ok=True, message_id=msg.get("id"))
        log_event(base, "archived", message=msg, actor=agent, path=str(archive))
        result["archive"] = str(archive)
        return result
    except Exception as exc:
        try:
            archive = archive_message(base, path, ok=False, message_id=(msg or {}).get("id") if isinstance(msg, dict) else None)
        except Exception:
            archive = path
        log_event(base, "failed", message=msg if isinstance(msg, dict) else None, actor=agent, reason=repr(exc), path=str(archive))
        return {"path": str(path), "ok": False, "error": repr(exc), "archive": str(archive)}


def poll_once(args):
    base = Path(args.base)
    agent = resolve_agent(base, args.agent)
    results = []
    for inbox_path in list_messages(base, agent):
        path = claim_message(base, agent, inbox_path)
        if path is None:
            continue
        results.append(_handle_claimed_message(args, base, agent, path))
    return results


def cmd_poll(args):
    print(json.dumps({"count": len(results := poll_once(args)), "results": results}, ensure_ascii=False, indent=2))


def _handle_receipt_message(args, base: Path, agent: str, path: Path) -> dict:
    config = _config_from_args(args)
    msg = None
    try:
        msg = read_message(path)
        validate_message(msg, config=config)
        if msg.get("to") != agent:
            raise ValidationError(f"target mismatch: {msg.get('to')} != {agent}")
        if has_seen(base, msg):
            archive = archive_message(base, path, ok=True, message_id=msg.get("id"))
            log_event(base, "duplicate", message=msg, actor=agent, path=str(archive))
            return {"path": str(path), "ok": True, "duplicate": True, "archive": str(archive), "message_id": msg.get("id"), "from": msg.get("from"), "to": msg.get("to"), "type": msg.get("type"), "subject": msg.get("subject"), "thread_id": msg.get("thread_id")}

        log_event(base, "received", message=msg, actor=agent, path=str(path))
        result = {"path": str(path), "ok": True, "message_id": msg.get("id"), "from": msg.get("from"), "to": msg.get("to"), "type": msg.get("type"), "subject": msg.get("subject"), "thread_id": msg.get("thread_id")}

        if msg.get("human_approval_required", False):
            log_event(base, "receipt_queued_for_human", message=msg, actor=agent, reason="human_approval_required", path=str(path))
            result["queued_for_human"] = True
            result["reason"] = "human_approval_required"
            return result

        msg_type = msg.get("type")
        if msg_type == "request" and not args.archive_requests_too:
            log_event(base, "receipt_queued_for_human", message=msg, actor=agent, reason="request_requires_human", path=str(path))
            result["queued_for_human"] = True
            result["reason"] = "request_requires_human"
            return result

        if msg_type not in RECEIPT_ARCHIVE_TYPES and not (msg_type == "request" and args.archive_requests_too):
            raise ValidationError(f"message type not receipt-archivable: {msg_type}")

        mark_seen(base, msg, actor=agent)
        log_event(base, "receipt_logged", message=msg, actor=agent, path=str(path))
        archive = archive_message(base, path, ok=True, message_id=msg.get("id"))
        log_event(base, "archived", message=msg, actor=agent, path=str(archive))
        result["archive"] = str(archive)
        return result
    except Exception as exc:
        try:
            archive = archive_message(base, path, ok=False, message_id=(msg or {}).get("id") if isinstance(msg, dict) else None)
        except Exception:
            archive = path
        log_event(base, "failed", message=msg if isinstance(msg, dict) else None, actor=agent, reason=repr(exc), path=str(archive))
        return {"path": str(path), "ok": False, "error": repr(exc), "archive": str(archive)}


def receipt_once(args):
    base = Path(args.base)
    agent = resolve_agent(base, args.agent)
    results = []
    for inbox_path in list_messages(base, agent):
        path = claim_message(base, agent, inbox_path)
        if path is None:
            continue
        results.append(_handle_receipt_message(args, base, agent, path))
    if getattr(args, "recover_processing", False):
        for path in sorted(processing_dir(base, agent).glob("*.json")):
            results.append(_handle_receipt_message(args, base, agent, path))
    return results


def _print_receipt_results(results: list[dict], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
        return
    for item in results:
        status = "ok" if item.get("ok") else "failed"
        action = "queued" if item.get("queued_for_human") else "archived" if item.get("archive") else "claimed"
        print(f"{status} {action} {item.get('message_id') or item.get('path')} from={item.get('from')} type={item.get('type')} subject={item.get('subject')}")


def cmd_receipt(args):
    while True:
        results = receipt_once(args)
        if results or args.once:
            _print_receipt_results(results, json_output=args.json)
        if args.once:
            return
        time.sleep(args.interval)


def cmd_watch(args):
    dispatch_action = getattr(args, "dispatch_action", None)
    while True:
        if dispatch_action:
            base = Path(args.base)
            agent = resolve_agent(base, args.agent)
            results = []
            for inbox_path in list_messages(base, agent):
                path = claim_message(base, agent, inbox_path)
                if path is None:
                    continue
                results.append(_handle_claimed_message(args, base, agent, path, dispatch_action=dispatch_action))
        else:
            results = poll_once(args)
        if results:
            print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False), flush=True)
        time.sleep(args.interval)


def cmd_dispatch(args):
    base = Path(args.base)
    agent = resolve_agent(base, args.agent)
    results = []
    for inbox_path in list_messages(base, agent):
        path = claim_message(base, agent, inbox_path)
        if path is None:
            continue
        results.append(_handle_claimed_message(args, base, agent, path, dispatch_action=args.action or "__default__"))
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))


def _message_metadata(path: Path, msg: dict) -> dict:
    return {
        "path": str(path),
        "id": msg.get("id"),
        "from": msg.get("from"),
        "to": msg.get("to"),
        "type": msg.get("type"),
        "subject": msg.get("subject"),
        "thread_id": msg.get("thread_id"),
        "needs_reply": msg.get("needs_reply"),
        "human_approval_required": msg.get("human_approval_required"),
    }


def _list_message_metadata(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        try:
            msg = read_message(path)
            rows.append(_message_metadata(path, msg))
        except Exception as exc:
            rows.append({"ok": False, "error": repr(exc), "path": str(path)})
    return rows


def cmd_pending(args):
    base = Path(args.base)
    agent = resolve_agent(base, args.agent)
    paths = list_messages(base, agent)
    if args.include_processing:
        paths += sorted(processing_dir(base, agent).glob("*.json"))
    rows = _list_message_metadata(paths)
    print(json.dumps({"count": len(rows), "messages": rows}, ensure_ascii=False, indent=2))


def cmd_queued(args):
    base = Path(args.base)
    agent = resolve_agent(base, args.agent)
    rows = _list_message_metadata(sorted(processing_dir(base, agent).glob("*.json")))
    print(json.dumps({"count": len(rows), "messages": rows}, ensure_ascii=False, indent=2))


def _parse_event_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_cutoff(days: int | None) -> datetime | None:
    if days is None or days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _iter_events(base: Path, *, days: int | None = None):
    events_dir = base / "events"
    cutoff = _event_cutoff(days)
    for path in sorted(events_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = _parse_event_timestamp(item.get("timestamp"))
            if cutoff and timestamp and timestamp < cutoff:
                continue
            yield item


def _thread_contact_match(event: dict, contact_id: str | None) -> bool:
    if not contact_id:
        return True
    return contact_id in {event.get("from"), event.get("to"), event.get("actor")}


def _scan_live_threads(base: Path) -> dict[str, dict]:
    by_thread: dict[str, dict] = {}
    for dirname, count_key in [
        ("inbox", "pending_count"),
        ("processing", "queued_count"),
    ]:
        root = base / dirname
        for path in sorted(root.glob("*/*.json")):
            try:
                msg = read_message(path)
            except Exception:
                continue
            tid = msg.get("thread_id")
            if not tid:
                continue
            row = by_thread.setdefault(tid, {
                "pending_count": 0,
                "queued_count": 0,
                "needs_reply": False,
                "participants": set(),
                "message_ids": set(),
            })
            row[count_key] += 1
            if msg.get("from"):
                row["participants"].add(msg.get("from"))
            if msg.get("to"):
                row["participants"].add(msg.get("to"))
            if msg.get("id"):
                row["message_ids"].add(msg.get("id"))
            # Conservative live-only inference: archived history is not inspected.
            if msg.get("needs_reply") is True or msg.get("type") == "request":
                row["needs_reply"] = True
    return by_thread


def cmd_threads(args):
    base = Path(args.base)
    contact_id = resolve_agent(base, args.contact) if args.contact else None
    event_types = set(args.event_type or [])
    all_events = [
        event for event in _iter_events(base, days=args.days)
        if event.get("thread_id") and _thread_contact_match(event, contact_id)
    ]
    events = [
        event for event in all_events
        if not event_types or event.get("event_type") in event_types
    ]
    failed_threads = {
        event["thread_id"] for event in all_events
        if event.get("event_type") in FAILED_EVENT_TYPES
    }
    live_threads = _scan_live_threads(base)
    by_thread: dict[str, dict] = {}
    for event in events:
        tid = event["thread_id"]
        row = by_thread.setdefault(tid, {
            "thread_id": tid,
            "events": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "last_event": None,
            "participants": set(),
            "message_ids": set(),
            "failed": False,
            "pending_count": 0,
            "queued_count": 0,
            "needs_reply": False,
        })
        row["events"] += 1
        timestamp = event.get("timestamp")
        if timestamp and (row["first_timestamp"] is None or timestamp < row["first_timestamp"]):
            row["first_timestamp"] = timestamp
        if timestamp and (row["last_timestamp"] is None or timestamp >= row["last_timestamp"]):
            row["last_timestamp"] = timestamp
            row["last_event"] = event.get("event_type")
        for key in ("from", "to", "actor"):
            if event.get(key):
                row["participants"].add(event.get(key))
        if event.get("message_id"):
            row["message_ids"].add(event.get("message_id"))
        if event.get("event_type") in FAILED_EVENT_TYPES:
            row["failed"] = True
    for tid, live in live_threads.items():
        if event_types and tid not in by_thread:
            continue
        if contact_id and contact_id not in live["participants"]:
            continue
        row = by_thread.setdefault(tid, {
            "thread_id": tid,
            "events": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "last_event": None,
            "participants": set(),
            "message_ids": set(),
            "failed": False,
            "pending_count": 0,
            "queued_count": 0,
            "needs_reply": False,
        })
        row["pending_count"] += live["pending_count"]
        row["queued_count"] += live["queued_count"]
        row["needs_reply"] = row["needs_reply"] or live["needs_reply"]
        row["participants"].update(live["participants"])
        row["message_ids"].update(live["message_ids"])
    for tid, row in by_thread.items():
        row["failed"] = row["failed"] or tid in failed_threads
    rows = []
    for row in by_thread.values():
        if args.failed and not row["failed"]:
            continue
        if args.needs_reply and not row["needs_reply"]:
            continue
        row["participants"] = sorted(row["participants"])
        row["message_ids"] = sorted(row["message_ids"])
        rows.append(row)
    rows = sorted(rows, key=lambda x: x.get("last_timestamp") or "", reverse=True)
    print(json.dumps({"count": len(rows), "threads": rows}, ensure_ascii=False, indent=2))


def cmd_timeline(args):
    base = Path(args.base)
    events = [
        {key: event.get(key) for key in TIMELINE_FIELDS if event.get(key) is not None}
        for event in _iter_events(base, days=args.days)
        if event.get("thread_id") == args.thread_id
    ]
    events.sort(key=lambda event: event.get("timestamp") or "")
    if args.markdown:
        print(f"# Timeline {args.thread_id}")
        print()
        if not events:
            print("_No events found._")
            return
        for event in events:
            parts = [event.get("event_type") or "event"]
            if event.get("actor"):
                parts.append(f"actor={event['actor']}")
            if event.get("message_id"):
                parts.append(f"message_id={event['message_id']}")
            if event.get("from") or event.get("to"):
                parts.append(f"{event.get('from', '?')} -> {event.get('to', '?')}")
            if event.get("reason"):
                parts.append(f"reason={event['reason']}")
            if event.get("path"):
                parts.append(f"path={event['path']}")
            print(f"- {event.get('timestamp', '')}: " + "; ".join(parts))
        return
    print(json.dumps({"thread_id": args.thread_id, "count": len(events), "events": events}, ensure_ascii=False, indent=2))


def _count_malformed_json(root: Path) -> int:
    count = 0
    for path in sorted(root.glob("*/*.json")):
        try:
            read_message(path)
        except Exception:
            count += 1
    return count


def _count_messages_by_agent(root: Path) -> dict[str, int]:
    counts = {}
    if not root.exists():
        return counts
    for path in sorted(root.iterdir()):
        if path.is_dir():
            counts[path.name] = len(list(path.glob("*.json")))
    return counts


def cmd_doctor(args):
    base = Path(args.base)
    try:
        contacts = load_contacts(base)
        expected_dirs = ["inbox", "processing", "archive/processed", "archive/failed", "events", "logs", "tmp", "locks", "state"]
        dirs = {name: (base / name).is_dir() for name in expected_dirs}
        malformed_counts = {
            "inbox": _count_malformed_json(base / "inbox"),
            "processing": _count_malformed_json(base / "processing"),
        }
        report = {
            "base": str(base),
            "dirs": dirs,
            "contacts_count": len(contacts.get("contacts", {})),
            "inbox_counts": _count_messages_by_agent(base / "inbox"),
            "processing_counts": _count_messages_by_agent(base / "processing"),
            "failed_archive_count": len(list((base / "archive" / "failed").glob("*.json"))),
            "events_files_count": len(list((base / "events").glob("*.jsonl"))),
            "malformed_json_count": sum(malformed_counts.values()),
            "malformed_json_counts": malformed_counts,
        }
    except Exception as exc:
        report = {"base": str(base), "ok": False, "error": repr(exc)}
    else:
        report["ok"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_contacts_list(args):
    data = load_contacts(Path(args.base))
    rows = []
    for cid, contact in sorted(data.get("contacts", {}).items()):
        rows.append({
            "id": cid,
            "display_name": contact.get("display_name"),
            "aliases": contact.get("aliases") or [],
            "transport": contact.get("transport"),
            "trust_level": contact.get("trust_level"),
        })
    print(json.dumps({"count": len(rows), "contacts": rows}, ensure_ascii=False, indent=2))


def cmd_contacts_show(args):
    base = Path(args.base)
    data = load_contacts(base)
    cid = resolve_contact(data, args.contact)
    print(json.dumps(data["contacts"][cid], ensure_ascii=False, indent=2))


def cmd_contacts_add(args):
    base = Path(args.base)
    allowed_types = args.allowed_type or sorted(["note", "request", "reply", "status", "alert", "handoff", "memory", "heartbeat"])
    contact = Contact(
        id=args.id,
        display_name=args.display_name,
        aliases=args.alias or [],
        transport=args.transport,
        allow_from=args.allow_from or [],
        allowed_types=allowed_types,
        trust_level=args.trust_level,
        notes=args.notes or "",
    )
    added = add_contact(base, contact)
    print(json.dumps(added, ensure_ascii=False, indent=2))


def cmd_contacts_remove(args):
    removed = remove_contact(Path(args.base), args.contact)
    print(json.dumps({"removed": removed}, ensure_ascii=False, indent=2))


def add_send_args(s):
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--type", default="note")
    s.add_argument("--subject", required=True)
    s.add_argument("--body", required=True)
    s.add_argument("--reply-to")
    s.add_argument("--thread-id")
    s.add_argument("--urgency", default="normal", choices=["low", "normal", "high"])
    s.add_argument("--needs-reply", action="store_true")
    s.add_argument("--idempotency-key")


def build_parser():
    p = argparse.ArgumentParser(prog="a2a-relay")
    p.add_argument("--base", default="/root/agent-mailbox")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--agent", action="append", required=True)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("send")
    add_send_args(s)
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("reply")
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to")
    s.add_argument("--message-file")
    s.add_argument("--reply-to")
    s.add_argument("--thread-id")
    s.add_argument("--subject")
    s.add_argument("--body", required=True)
    s.add_argument("--urgency", default="normal", choices=["low", "normal", "high"])
    s.set_defaults(func=cmd_reply)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)
    s = task_sub.add_parser("send")
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--context", action="append")
    s.add_argument("--constraint", action="append")
    s.add_argument("--require-output", action="append")
    s.add_argument("--capability", action="append")
    s.add_argument("--profile", default="read-only-fast")
    s.add_argument("--approval-required", action="store_true")
    s.add_argument("--urgency", default="normal", choices=["low", "normal", "high"])
    s.add_argument("--thread-id")
    s.add_argument("--idempotency-key")
    s.set_defaults(func=cmd_task_send)

    for name, func in [("poll", cmd_poll), ("watch", cmd_watch)]:
        s = sub.add_parser(name)
        s.add_argument("--agent", required=True)
        s.add_argument("--allow-from", action="append")
        s.add_argument("--allowed-type", action="append", default=sorted(["note", "request", "reply", "status", "alert", "handoff", "memory", "heartbeat"]))
        s.add_argument("--max-body-chars", type=int, default=20000)
        s.add_argument("--ack", action="store_true")
        if name == "watch":
            s.add_argument("--interval", type=float, default=10.0)
            s.add_argument("--dispatch-action", dest="dispatch_action")
        s.set_defaults(func=func)

    s = sub.add_parser("dispatch")
    s.add_argument("--agent", required=True)
    s.add_argument("--action")
    s.add_argument("--allow-from", action="append")
    s.add_argument("--allowed-type", action="append", default=sorted(["note", "request", "reply", "status", "alert", "handoff", "memory", "heartbeat"]))
    s.add_argument("--max-body-chars", type=int, default=20000)
    s.add_argument("--ack", action="store_true")
    s.set_defaults(func=cmd_dispatch)

    s = sub.add_parser("receipt", aliases=["watch-receipts"])
    s.add_argument("--agent", required=True)
    s.add_argument("--allow-from", action="append", required=True)
    s.add_argument("--allowed-type", action="append", default=RECEIPT_DEFAULT_TYPES)
    s.add_argument("--max-body-chars", type=int, default=20000)
    s.add_argument("--archive-requests-too", action="store_true")
    s.add_argument("--recover-processing", action="store_true")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=float, default=10.0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_receipt)

    s = sub.add_parser("pending")
    s.add_argument("--agent", required=True)
    s.add_argument("--include-processing", action="store_true")
    s.set_defaults(func=cmd_pending)

    s = sub.add_parser("queued")
    s.add_argument("--agent", required=True)
    s.set_defaults(func=cmd_queued)

    s = sub.add_parser("threads")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--contact")
    s.add_argument("--event-type", action="append")
    s.add_argument("--failed", action="store_true")
    s.add_argument("--needs-reply", action="store_true")
    s.set_defaults(func=cmd_threads)

    s = sub.add_parser("timeline")
    s.add_argument("thread_id")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--markdown", action="store_true")
    s.set_defaults(func=cmd_timeline)

    s = sub.add_parser("doctor")
    s.set_defaults(func=cmd_doctor)

    contacts = sub.add_parser("contacts")
    csub = contacts.add_subparsers(dest="contacts_cmd", required=True)

    s = csub.add_parser("list")
    s.set_defaults(func=cmd_contacts_list)

    s = csub.add_parser("show")
    s.add_argument("contact")
    s.set_defaults(func=cmd_contacts_show)

    s = csub.add_parser("add")
    s.add_argument("--id", required=True)
    s.add_argument("--display-name")
    s.add_argument("--alias", action="append")
    s.add_argument("--transport", default="filesystem")
    s.add_argument("--allow-from", action="append")
    s.add_argument("--allowed-type", action="append")
    s.add_argument("--trust-level", default="trusted-filesystem")
    s.add_argument("--notes")
    s.set_defaults(func=cmd_contacts_add)

    s = csub.add_parser("remove")
    s.add_argument("contact")
    s.set_defaults(func=cmd_contacts_remove)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ContactError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
