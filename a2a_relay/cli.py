from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .core import (
    RelayConfig,
    ValidationError,
    archive_message,
    claim_message,
    has_seen,
    init_mailbox,
    list_messages,
    log_event,
    make_message,
    mark_seen,
    read_message,
    send_message,
    validate_message,
)


def cmd_init(args):
    init_mailbox(Path(args.base), args.agent)
    print(Path(args.base).resolve())


def cmd_send(args):
    msg = make_message(
        args.from_, args.to, args.type, args.subject, args.body,
        reply_to=args.reply_to, thread_id=args.thread_id,
        urgency=args.urgency, needs_reply=args.needs_reply,
        idempotency_key=args.idempotency_key,
    )
    path = send_message(Path(args.base), msg)
    print(path)


def cmd_reply(args):
    original = None
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
    msg = make_message(
        args.from_, to, "reply", subject or "Re", args.body,
        reply_to=reply_to, thread_id=thread_id,
        urgency=args.urgency,
    )
    path = send_message(Path(args.base), msg)
    log_event(Path(args.base), "replied", message=msg.to_json_dict(), actor=args.from_, extra={"reply_to": reply_to})
    print(path)


def _config_from_args(args) -> RelayConfig:
    return RelayConfig(
        allow_from=set(args.allow_from or []),
        allowed_types=set(args.allowed_type or []),
        max_body_chars=args.max_body_chars,
        trusted_filesystem_unsigned=True,
    )


def poll_once(args):
    base = Path(args.base)
    config = _config_from_args(args)
    results = []
    for inbox_path in list_messages(base, args.agent):
        path = claim_message(base, args.agent, inbox_path)
        if path is None:
            continue
        msg = None
        try:
            msg = read_message(path)
            validate_message(msg, config=config)
            if msg.get("to") != args.agent:
                raise ValidationError(f"target mismatch: {msg.get('to')} != {args.agent}")
            if has_seen(base, msg):
                archive = archive_message(base, path, ok=True, message_id=msg.get("id"))
                log_event(base, "duplicate", message=msg, actor=args.agent, path=str(archive))
                results.append({"path": str(path), "ok": True, "duplicate": True, "archive": str(archive), "message": msg})
                continue
            log_event(base, "received", message=msg, actor=args.agent, path=str(path))
            ack_path = None
            if args.ack:
                ack = make_message(
                    args.agent, msg["from"], "status", f"ACK: {msg.get('subject','')}",
                    f"{args.agent} received: {msg.get('subject','')}",
                    reply_to=msg.get("id"), thread_id=msg.get("thread_id"),
                )
                ack_path = send_message(base, ack)
                log_event(base, "acked", message=msg, actor=args.agent, extra={"ack_path": str(ack_path)})
            mark_seen(base, msg, actor=args.agent)
            archive = archive_message(base, path, ok=True, message_id=msg.get("id"))
            log_event(base, "archived", message=msg, actor=args.agent, path=str(archive))
            results.append({"path": str(path), "ok": True, "ack": str(ack_path) if ack_path else None, "archive": str(archive), "message": msg})
        except Exception as exc:
            try:
                archive = archive_message(base, path, ok=False, message_id=(msg or {}).get("id") if isinstance(msg, dict) else None)
            except Exception:
                archive = path
            log_event(base, "failed", message=msg if isinstance(msg, dict) else None, actor=args.agent, reason=repr(exc), path=str(archive))
            results.append({"path": str(path), "ok": False, "error": repr(exc), "archive": str(archive)})
    return results


def cmd_poll(args):
    print(json.dumps({"count": len(results := poll_once(args)), "results": results}, ensure_ascii=False, indent=2))


def cmd_watch(args):
    while True:
        results = poll_once(args)
        if results:
            print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False), flush=True)
        time.sleep(args.interval)


def cmd_pending(args):
    base = Path(args.base)
    rows = []
    for path in list_messages(base, args.agent):
        try:
            msg = read_message(path)
            rows.append({
                "path": str(path),
                "id": msg.get("id"),
                "from": msg.get("from"),
                "to": msg.get("to"),
                "type": msg.get("type"),
                "subject": msg.get("subject"),
                "thread_id": msg.get("thread_id"),
                "needs_reply": msg.get("needs_reply"),
            })
        except Exception as exc:
            rows.append({"path": str(path), "error": repr(exc)})
    print(json.dumps({"count": len(rows), "messages": rows}, ensure_ascii=False, indent=2))


def cmd_threads(args):
    base = Path(args.base)
    events = []
    events_dir = base / "events"
    for path in sorted(events_dir.glob("*.jsonl"))[-args.days:]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("thread_id"):
                events.append(item)
    by_thread: dict[str, dict] = {}
    for event in events:
        tid = event["thread_id"]
        row = by_thread.setdefault(tid, {"thread_id": tid, "events": 0, "last_timestamp": None, "last_event": None})
        row["events"] += 1
        row["last_timestamp"] = event.get("timestamp")
        row["last_event"] = event.get("event_type")
    rows = sorted(by_thread.values(), key=lambda x: x.get("last_timestamp") or "", reverse=True)
    print(json.dumps({"count": len(rows), "threads": rows}, ensure_ascii=False, indent=2))


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

    for name, func in [("poll", cmd_poll), ("watch", cmd_watch)]:
        s = sub.add_parser(name)
        s.add_argument("--agent", required=True)
        s.add_argument("--allow-from", action="append")
        s.add_argument("--allowed-type", action="append", default=sorted(["note", "request", "reply", "status", "alert", "handoff", "memory", "heartbeat"]))
        s.add_argument("--max-body-chars", type=int, default=20000)
        s.add_argument("--ack", action="store_true")
        if name == "watch":
            s.add_argument("--interval", type=float, default=10.0)
        s.set_defaults(func=func)

    s = sub.add_parser("pending")
    s.add_argument("--agent", required=True)
    s.set_defaults(func=cmd_pending)

    s = sub.add_parser("threads")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(func=cmd_threads)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
