"""posts for the brackets and announcements channels, held for the owners to review.

instead of going public at once, a post is stored in a queue, the discord bot shows it to the staff
channel (tagging the owners) and publishes it `hold_hours` later. the owners can send it earlier or
cancel it with buttons. if a newer version of the same post (same key, like a bracket that changed
after a match) comes in while it is still held, it replaces the held one.

this is used by the image generator (its own process) and by the bot, so every change to the queue
is made under a file lock.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import time
from pathlib import Path

from server import config
from tournament.storage import SEASONS_DIR
from tournament.webhook import Webhook

try:
    import fcntl
except ImportError:  # not on linux, there the lock does nothing.
    fcntl = None

QUEUE_DIR = SEASONS_DIR / "queue"
LOCK_FILE = QUEUE_DIR / ".lock"

PENDING = "PENDING"
SENT = "SENT"
CANCELLED = "CANCELLED"

MESSAGE_LIMIT = 1900  # discord's message limit is 2000


# ---- settings ----


def hold_hours() -> float:
    try:
        return float(config.discord.get("hold_hours", 0))
    except (TypeError, ValueError):
        return 0.0


def staff_channel_id() -> int:
    try:
        return int(config.discord.get("staff_channel_id", 0))
    except (TypeError, ValueError):
        return 0


def hold_enabled() -> bool:
    """posts are held only when the bot runs (it does the publishing) and a staff channel is set."""
    return bool(config.discord.enable and staff_channel_id() and hold_hours() > 0)


# ---- the queue ----


@contextlib.contextmanager
def locked():
    """one writer at a time, across the processes."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _record_path(post_id: str) -> Path:
    return QUEUE_DIR / f"{post_id}.json"


def _image_path(record: dict) -> Path | None:
    return QUEUE_DIR / record["image"] if record.get("image") else None


def _write(record: dict) -> None:
    path = _record_path(record["id"])
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, indent=4), encoding="utf-8")
    os.replace(temp, path)


def get(post_id: str) -> dict | None:
    try:
        return json.loads(_record_path(post_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_pending() -> list[dict]:
    """all the posts that are still held, oldest first."""
    records = []
    for path in QUEUE_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("state") == PENDING:
            records.append(record)
    return sorted(records, key=lambda r: r["created_at"])


def find_pending(season_id: str, key: str) -> dict | None:
    for record in load_pending():
        if record["season_id"] == season_id and record["key"] == key:
            return record
    return None


def image_bytes(record: dict) -> bytes | None:
    path = _image_path(record)
    if path and path.exists():
        return path.read_bytes()
    return None


def update(post_id: str, **changes) -> dict | None:
    """changes fields of a record (a record that is gone stays gone)."""
    with locked():
        record = get(post_id)
        if record is None:
            return None
        record.update(changes)
        _write(record)
        return record


# ---- sending ----


def _publish_now(
    webhook: Webhook,
    type: str,
    key: str,
    filename: str | None,
    image: bytes | None,
    content: str | None,
) -> None:
    """posts to the channel, a post that was already sent under this key is edited instead."""
    files = webhook.create(filename, io.BytesIO(image)) if image and filename else None
    if webhook.get(key):
        webhook.edit(key, files, content=content)
        return
    webhook.send(type, key, files, content=content)


def submit(
    webhook: Webhook,
    type: str,
    key: str,
    label: str,
    filename: str | None = None,
    image: bytes | None = None,
    content: str | None = None,
    once: bool = False,
) -> None:
    """sends a post to the channel of type, or holds it for the owners when holding is on.

    key: the post's identity, a new version of it edits the old one (or replaces it, while held).
    label: what the owners read in the staff channel.
    once: do nothing if this post was already sent (or is held).
    """
    if not hold_enabled():
        if once and webhook.get(key):
            return
        _publish_now(webhook, type, key, filename, image, content)
        return

    with locked():
        existing = find_pending(webhook.season_id, key)
        if once and (existing or webhook.get(key)):
            return

        if existing:
            record = existing
            record["revision"] += 1
        else:
            now = time.time()
            record = {
                "id": f"{int(now)}-{secrets.token_hex(3)}",
                "season_id": webhook.season_id,
                "type": type,
                "key": key,
                "created_at": now,
                "publish_at": now + hold_hours() * 3600,
                "state": PENDING,
                "revision": 1,
                "synced_revision": 0,  # the revision the staff message shows
                "staff_message_id": None,
                "image": None,
            }

        record.update({"label": label, "filename": filename, "content": content})
        if image:
            record["image"] = f"{record['id']}.png"
            (QUEUE_DIR / record["image"]).write_bytes(image)
        _write(record)


def publish(post_id: str) -> bool:
    """publishes a held post now, returns whether it was still held."""
    with locked():
        record = get(post_id)
        if record is None or record["state"] != PENDING:
            return False
        _publish_now(
            Webhook(record["season_id"]),
            record["type"],
            record["key"],
            record.get("filename"),
            image_bytes(record),
            record.get("content"),
        )
        record["state"] = SENT
        record["resolved_at"] = time.time()
        _write(record)
        _remove_image(record)
        return True


def cancel(post_id: str) -> bool:
    """throws a held post away, returns whether it was still held."""
    with locked():
        record = get(post_id)
        if record is None or record["state"] != PENDING:
            return False
        record["state"] = CANCELLED
        record["resolved_at"] = time.time()
        _write(record)
        _remove_image(record)
        return True


def cancel_season(season_id: str) -> list[dict]:
    """throws away all the held posts of a season (its tournament was cancelled)."""
    cancelled = []
    with locked():
        for record in load_pending():
            if record["season_id"] != season_id:
                continue
            record["state"] = CANCELLED
            record["resolved_at"] = time.time()
            _write(record)
            _remove_image(record)
            cancelled.append(record)
    return cancelled


def _remove_image(record: dict) -> None:
    path = _image_path(record)
    if path:
        with contextlib.suppress(OSError):
            path.unlink()


def announce_lines(season_id: str, lines: list[str], label: str, key_prefix: str) -> None:
    """posts text lines to the announcements channel, split in messages that fit discord's limit."""
    webhook = Webhook(season_id)
    chunk, number = "", 1
    for line in lines:
        if chunk and len(chunk) + len(line) + 1 > MESSAGE_LIMIT:
            submit(webhook, "announcements", f"{key_prefix}-{number}", label, content=chunk)
            chunk, number = "", number + 1
        chunk += ("\n" if chunk else "") + line
    if chunk:
        submit(webhook, "announcements", f"{key_prefix}-{number}", label, content=chunk)
