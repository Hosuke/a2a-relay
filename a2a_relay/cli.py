from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .core import archive_message, init_mailbox, list_messages, make_message, read_message, send_message


def cmd_init(args):
    init_mailbox(Path(args.base), args.agent)
    print(Path(args.base).resolve())


def cmd_send(args):
    msg = make_message(
        args.from_, args.to, args.type, args.subject, args.body,
        reply_to=args.reply_to, thread_id=args.thread_id,
        urgency=args.urgency, needs_reply=args.needs_reply,
    )
    path = send_message(Path(args.base), msg)
    print(path)


def poll_once(args):
    base = Path(args.base)
    allow = set(args.allow_from or [])
    results = []
    for path in list_messages(base, args.agent):
        try:
            msg = read_message(path)
            ok = msg.get("to") == args.agent and (not allow or msg.get("from") in allow)
            if args.ack and ok:
                ack = make_message(
                    args.agent, msg["from"], "status", f"ACK: {msg.get('subject','')}",
                    f"{args.agent} received: {msg.get('subject','')}",
                    reply_to=msg.get("id"), thread_id=msg.get("thread_id"),
                )
                ack_path = send_message(base, ack)
            else:
                ack_path = None
            archive = archive_message(base, path, ok=ok)
            results.append({"path": str(path), "ok": ok, "ack": str(ack_path) if ack_path else None, "archive": str(archive), "message": msg})
        except Exception as exc:
            archive = archive_message(base, path, ok=False)
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


def build_parser():
    p = argparse.ArgumentParser(prog="a2a-relay")
    p.add_argument("--base", default="/root/agent-mailbox")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--agent", action="append", required=True)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("send")
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--type", default="note")
    s.add_argument("--subject", required=True)
    s.add_argument("--body", required=True)
    s.add_argument("--reply-to")
    s.add_argument("--thread-id")
    s.add_argument("--urgency", default="normal", choices=["low", "normal", "high"])
    s.add_argument("--needs-reply", action="store_true")
    s.set_defaults(func=cmd_send)

    for name, func in [("poll", cmd_poll), ("watch", cmd_watch)]:
        s = sub.add_parser(name)
        s.add_argument("--agent", required=True)
        s.add_argument("--allow-from", action="append")
        s.add_argument("--ack", action="store_true")
        if name == "watch":
            s.add_argument("--interval", type=float, default=10.0)
        s.set_defaults(func=func)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
