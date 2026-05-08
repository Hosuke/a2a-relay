from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

MESSAGE_TYPES = {"note", "request", "reply", "status", "alert", "handoff", "memory", "heartbeat"}
URGENCIES = {"low", "normal", "high"}
REQUIRED_FIELDS = {"version", "id", "from", "to", "type", "subject", "body", "created_at"}
RESERVED_SIGNATURE_FIELDS = {"signature", "key_id", "nonce", "signed_at", "expires_at"}
DEFAULT_MAX_BODY_CHARS = 20000


class ValidationError(ValueError):
    """Raised when an A2A message fails schema or policy validation."""


@dataclass
class RelayConfig:
    allow_from: set[str] = field(default_factory=set)
    allowed_types: set[str] = field(default_factory=lambda: set(MESSAGE_TYPES))
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS
    trusted_filesystem_unsigned: bool = True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "agent"


def message_id(sender: str, recipient: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
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
    idempotency_key: Optional[str] = None
    signature: Optional[str] = None
    key_id: Optional[str] = None
    nonce: Optional[str] = None
    signed_at: Optional[str] = None
    expires_at: Optional[str] = None

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["from"] = data.pop("from_")
        return {k: v for k, v in data.items() if v is not None}


def make_message(sender: str, recipient: str, typ: str, subject: str, body: str,
                 *, reply_to: str | None = None, thread_id: str | None = None,
                 urgency: str = "normal", needs_reply: bool = False,
                 idempotency_key: str | None = None) -> A2AMessage:
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
        idempotency_key=idempotency_key,
    )


def validate_message(msg: dict, *, config: RelayConfig | None = None) -> None:
    config = config or RelayConfig()
    missing = sorted(REQUIRED_FIELDS - set(msg))
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")
    if msg.get("version") != "a2a.v1":
        raise ValidationError(f"unsupported version: {msg.get('version')!r}")
    for key in ["id", "from", "to", "type", "subject", "body", "created_at"]:
        if not isinstance(msg.get(key), str) or not msg.get(key):
            raise ValidationError(f"field {key!r} must be a non-empty string")
    if msg["type"] not in MESSAGE_TYPES:
        raise ValidationError(f"unknown message type: {msg['type']}")
    if msg["type"] not in config.allowed_types:
        raise ValidationError(f"message type not allowed: {msg['type']}")
    urgency = msg.get("urgency", "normal")
    if urgency not in URGENCIES:
        raise ValidationError(f"unknown urgency: {urgency}")
    if config.allow_from and msg["from"] not in config.allow_from:
        raise ValidationError(f"sender not allowed: {msg['from']}")
    body = msg.get("body", "")
    if len(body) > config.max_body_chars:
        raise ValidationError(f"body too large: {len(body)} > {config.max_body_chars}")
    if msg.get("attachments"):
        raise ValidationError("attachments are not supported in v0.2")
    _parse_timestamp(msg["created_at"], field="created_at")
    if msg.get("expires_at"):
        expires_at = _parse_timestamp(msg["expires_at"], field="expires_at")
        if expires_at <= datetime.now(timezone.utc):
            raise ValidationError("message expired")
    for key in ["needs_reply", "human_approval_required"]:
        if key in msg and not isinstance(msg[key], bool):
            raise ValidationError(f"field {key!r} must be boolean")


def _parse_timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValidationError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{field} must include timezone")
    return parsed


def inbox_dir(base: Path, agent_id: str) -> Path:
    return base / "inbox" / safe_name(agent_id)


def processing_dir(base: Path, agent_id: str) -> Path:
    return base / "processing" / safe_name(agent_id)


def init_mailbox(base: Path, agents: Iterable[str]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for sub in ["archive/processed", "archive/failed", "events", "logs", "tmp", "locks", "state", "processing"]:
        (base / sub).mkdir(parents=True, exist_ok=True)
    agent_map = {}
    for agent in agents:
        inbox = inbox_dir(base, agent)
        inbox.mkdir(parents=True, exist_ok=True)
        processing = processing_dir(base, agent)
        processing.mkdir(parents=True, exist_ok=True)
        agent_map[agent] = {"inbox": str(inbox), "processing": str(processing), "safe_name": safe_name(agent)}
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
    data = msg.to_json_dict()
    validate_message(data)
    dest = inbox_dir(base, msg.to) / f"{msg.id}.json"
    atomic_write_json(dest, data)
    log_event(base, "sent", message=data, actor=msg.from_)
    return dest


def read_message(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_messages(base: Path, agent_id: str) -> list[Path]:
    inbox = inbox_dir(base, agent_id)
    if not inbox.exists():
        return []
    return sorted(inbox.glob("*.json"))


def claim_message(base: Path, agent_id: str, path: Path) -> Path | None:
    dest_dir = processing_dir(base, agent_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    try:
        path.replace(dest)
    except FileNotFoundError:
        return None
    return dest


def archive_message(base: Path, path: Path, ok: bool = True, *, message_id: str | None = None) -> Path:
    bucket = "processed" if ok else "failed"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    mid = safe_name(message_id or path.stem)
    dest = base / "archive" / bucket / f"{ts}_{mid}_{path.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    path.replace(dest)
    return dest


def log_event(base: Path, event_type: str, *, message: dict | None = None, actor: str | None = None,
              reason: str | None = None, path: str | None = None, extra: dict | None = None) -> Path:
    events_dir = base / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "event_id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}",
        "event_type": event_type,
        "timestamp": now_iso(),
        "actor": actor,
        "message_id": message.get("id") if message else None,
        "thread_id": message.get("thread_id") if message else None,
        "from": message.get("from") if message else None,
        "to": message.get("to") if message else None,
        "reason": reason,
        "path": path,
    }
    if extra:
        event.update(extra)
    out = events_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in event.items() if v is not None}, ensure_ascii=False) + "\n")
    return out


def seen_path(base: Path) -> Path:
    return base / "state" / "seen.jsonl"


def seen_keys_for(msg: dict) -> list[str]:
    keys = [f"id:{msg['id']}"]
    if msg.get("idempotency_key"):
        keys.append(f"idem:{msg['idempotency_key']}")
    return keys


def load_seen(base: Path) -> set[str]:
    path = seen_path(base)
    if not path.exists():
        return set()
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if item.get("key"):
                seen.add(item["key"])
        except json.JSONDecodeError:
            continue
    return seen


def has_seen(base: Path, msg: dict) -> bool:
    seen = load_seen(base)
    return any(key in seen for key in seen_keys_for(msg))


def mark_seen(base: Path, msg: dict, *, actor: str | None = None) -> None:
    path = seen_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for key in seen_keys_for(msg):
            fh.write(json.dumps({"key": key, "message_id": msg["id"], "actor": actor, "timestamp": now_iso()}, ensure_ascii=False) + "\n")
