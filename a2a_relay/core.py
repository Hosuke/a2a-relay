from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Optional

MESSAGE_TYPES = {"note", "request", "reply", "status", "alert", "handoff", "memory", "heartbeat"}
URGENCIES = {"low", "normal", "high"}


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "agent"


def message_id(sender: str, recipient: str) -> str:
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    return f"msg_{ts}_{safe_name(sender)}_to_{safe_name(recipient)}"


@dataclass
class A2AMessage:
    version: str
    id: str
    from_: str
    to: str
    type: str
    subject: str
    body: str
    created_at: str
    urgency: str = "normal"
    needs_reply: bool = False
    reply_to: Optional[str] = None
    thread_id: Optional[str] = None
    attachments: list = field(default_factory=list)
    capabilities_requested: list = field(default_factory=list)
    human_approval_required: bool = False
    status: str = "new"

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["from"] = data.pop("from_")
        return data


def make_message(sender: str, recipient: str, typ: str, subject: str, body: str,
                 *, reply_to: str | None = None, thread_id: str | None = None,
                 urgency: str = "normal", needs_reply: bool = False) -> A2AMessage:
    if typ not in MESSAGE_TYPES:
        raise ValueError(f"unknown message type: {typ}")
    if urgency not in URGENCIES:
        raise ValueError(f"unknown urgency: {urgency}")
    mid = message_id(sender, recipient)
    return A2AMessage(
        version="a2a.v1",
        id=mid,
        from_=sender,
        to=recipient,
        type=typ,
        subject=subject,
        body=body,
        created_at=now_iso(),
        urgency=urgency,
        needs_reply=needs_reply,
        reply_to=reply_to,
        thread_id=thread_id or f"thread_{safe_name(sender)}_{safe_name(recipient)}_{safe_name(subject)[:40]}",
    )


def inbox_dir(base: Path, agent_id: str) -> Path:
    return base / "inbox" / safe_name(agent_id)


def init_mailbox(base: Path, agents: Iterable[str]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for sub in ["archive/processed", "archive/failed", "logs", "tmp", "locks"]:
        (base / sub).mkdir(parents=True, exist_ok=True)
    agent_map = {}
    for agent in agents:
        inbox = inbox_dir(base, agent)
        inbox.mkdir(parents=True, exist_ok=True)
        agent_map[agent] = {"inbox": str(inbox), "safe_name": safe_name(agent)}
    (base / "agents.json").write_text(json.dumps({"agents": agent_map}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = path.parent.parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="a2a_", suffix=".json", dir=str(tmp_dir))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def send_message(base: Path, msg: A2AMessage) -> Path:
    dest = inbox_dir(base, msg.to) / f"{msg.id}.json"
    atomic_write_json(dest, msg.to_json_dict())
    return dest


def read_message(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_messages(base: Path, agent_id: str) -> list[Path]:
    inbox = inbox_dir(base, agent_id)
    if not inbox.exists():
        return []
    return sorted(inbox.glob("*.json"))


def archive_message(base: Path, path: Path, ok: bool = True) -> Path:
    bucket = "processed" if ok else "failed"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = base / "archive" / bucket / f"{ts}_{path.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    path.replace(dest)
    return dest
