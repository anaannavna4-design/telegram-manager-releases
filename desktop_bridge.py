from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app_paths import runtime_dir
from config import PERSISTENT_QUEUE_LIMIT
from queue_store import PersistentQueue, row_to_item


_QUEUE = None


def _queue() -> PersistentQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = PersistentQueue(PERSISTENT_QUEUE_LIMIT)
    return _QUEUE


def load_desktop_queue() -> list[dict]:
    """Return durable ready-to-publish comments for the Desktop UI."""
    return _queue().list_ready()


def load_queue_stats() -> dict:
    """Small status snapshot for counters in the GUI."""
    return _queue().stats()


def submit_desktop_command(action: str, key: str, *, comment: str | None = None) -> str:
    """Persist a Desktop action so it survives a GUI/engine crash."""
    return _queue().submit_command(action, key, comment)


def consume_desktop_commands() -> list[dict]:
    """Claim pending UI commands.

    Commands stay in SQLite until ack_desktop_command() is called.
    A crash while processing automatically makes them retryable later.
    """
    return _queue().claim_commands()


def ack_desktop_command(command_id: str) -> None:
    _queue().ack_command(command_id)


def retry_desktop_command(command_id: str, error: str = "") -> None:
    _queue().retry_command(command_id, error)


class DesktopBridge:
    """SQLite-backed bridge between the Windows GUI and engine.

    The old desktop_queue.json path is intentionally no longer used. This
    avoids WinError 5 races and preserves ready comments across restarts.
    """

    def __init__(self, clear_queue: bool = False):
        self.queue = _queue()
        # Only the engine constructs DesktopBridge, so restart recovery belongs here.
        self.queue.recover_after_restart()
        self._migrate_legacy_json_queue()
        # Kept only for API compatibility. A commercial build must never wipe
        # the queue just because the engine starts.
        if clear_queue:
            self.clear()

    def _migrate_legacy_json_queue(self) -> None:
        """Import desktop_queue.json once when upgrading from 1.1.x."""
        legacy = runtime_dir() / "desktop_queue.json"
        migrated = runtime_dir() / "desktop_queue.migrated.json"
        if not legacy.exists() or migrated.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return
            imported = 0
            for row in data:
                if not isinstance(row, dict) or not row.get("key"):
                    continue
                item = SimpleNamespace(
                    key=str(row.get("key") or ""),
                    account=str(row.get("account") or ""),
                    chat_id=int(row.get("chat_id") or 0),
                    message_id=int(row.get("message_id") or 0),
                    chat_title=str(row.get("chat_title") or ""),
                    post_text=str(row.get("post_text") or ""),
                    comment=str(row.get("comment") or ""),
                )
                self.queue.upsert_ready(
                    item,
                    autopilot=bool(row.get("autopilot")),
                    status=str(row.get("status") or "Ожидает"),
                )
                imported += 1
            if imported:
                legacy.replace(migrated)
                print(f"[QUEUE] Импортировано из старой JSON-очереди: {imported}")
        except Exception as exc:
            print(f"[QUEUE] Не удалось импортировать desktop_queue.json: {exc}")
    def clear(self) -> None:
        # Explicit clear only. Startup now passes clear_queue=False.
        for row in self.queue.list_ready():
            self.queue.remove(str(row.get("key") or ""))

    def enqueue_ai(self, item, *, account: str, autopilot: bool = False) -> str:
        return self.queue.enqueue_ai(item, account=account, autopilot=autopilot)

    def has_capacity(self) -> bool:
        return self.queue.has_capacity()

    def claim_next_ai(self, account: str):
        row = self.queue.claim_next_ai(account)
        return row_to_item(row) if row else None

    def requeue_ai(self, key: str, error: str = "") -> None:
        self.queue.requeue_ai(key, error)

    def upsert(self, item, *, autopilot: bool = False, status: str = "Ожидает") -> None:
        self.queue.upsert_ready(item, autopilot=autopilot, status=status)

    def set_status(self, key: str, status: str, *, clear_error: bool = False) -> None:
        self.queue.set_status(key, status, clear_error=clear_error)

    def set_error(self, key: str, status: str, error: str) -> None:
        self.queue.set_error(key, status, error)

    def remove(self, key: str) -> None:
        self.queue.remove(key)

    def get_item(self, key: str):
        row = self.queue.get(key)
        return row_to_item(row) if row else None

    def list_ready_items(self, account: str | None = None):
        return [row_to_item(row) for row in self.queue.list_ready(account)]

    def stats(self) -> dict:
        return self.queue.stats()
