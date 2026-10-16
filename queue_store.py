from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from app_paths import runtime_dir


DB_NAME = "neurocomment_queue.sqlite3"


class PersistentQueue:
    """Durable task/command storage shared by Desktop UI and engine.

    SQLite replaces the old volatile asyncio queue and JSON desktop queue.
    WAL mode lets the UI read while the engine writes. Each method opens a
    short-lived connection, so the GUI and engine can live in separate
    processes safely.
    """

    def __init__(self, max_tasks: int = 2000):
        self.path = runtime_dir() / DB_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_tasks = max(100, int(max_tasks or 2000))
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    key TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    chat_title TEXT NOT NULL DEFAULT '',
                    post_text TEXT NOT NULL DEFAULT '',
                    comment TEXT NOT NULL DEFAULT '',
                    autopilot INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'queued_ai',
                    status TEXT NOT NULL DEFAULT 'Ожидает AI',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_stage_account
                    ON tasks(stage, account, created_at);

                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    task_key TEXT NOT NULL,
                    comment TEXT,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_commands_state_created
                    ON commands(state, created_at);

                CREATE TABLE IF NOT EXISTS captcha_challenges (
                    id TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    chat_title TEXT NOT NULL DEFAULT '',
                    sender_id INTEGER NOT NULL DEFAULT 0,
                    sender_name TEXT NOT NULL DEFAULT '',
                    sender_username TEXT NOT NULL DEFAULT '',
                    challenge_text TEXT NOT NULL DEFAULT '',
                    buttons_json TEXT NOT NULL DEFAULT '[]',
                    media_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Ожидает ответа',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_captcha_account_status
                    ON captcha_challenges(account, status, created_at);
                """
            )

    def recover_after_restart(self) -> None:
        now = time.time()
        with self._lock, self._connect() as con:
            con.execute(
                """
                UPDATE tasks
                   SET stage='queued_ai',
                       status='Ожидает AI после перезапуска',
                       updated_at=?
                 WHERE stage='processing_ai'
                """,
                (now,),
            )
            con.execute(
                """
                UPDATE commands
                   SET state='pending',
                       updated_at=?
                 WHERE state='processing'
                """,
                (now,),
            )

    def count_tasks(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
            return int(row["n"] or 0)

    def has_capacity(self) -> bool:
        return self.count_tasks() < self.max_tasks

    def enqueue_ai(self, item, *, account: str, autopilot: bool = False) -> str:
        """Return inserted, exists, or full."""
        now = time.time()
        key = str(getattr(item, "key", "") or "")
        if not key:
            raise ValueError("Task key is empty")

        with self._lock, self._connect() as con:
            existing = con.execute(
                "SELECT key FROM tasks WHERE key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                return "exists"

            row = con.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
            if int(row["n"] or 0) >= self.max_tasks:
                return "full"

            con.execute(
                """
                INSERT INTO tasks (
                    key, account, chat_id, message_id, chat_title,
                    post_text, comment, autopilot, stage, status,
                    attempts, next_attempt_at, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued_ai', 'Ожидает AI',
                          0, 0, ?, ?, '')
                """,
                (
                    key,
                    str(account),
                    int(getattr(item, "chat_id", 0) or 0),
                    int(getattr(item, "message_id", 0) or 0),
                    str(getattr(item, "chat_title", "") or ""),
                    str(getattr(item, "post_text", "") or ""),
                    str(getattr(item, "comment", "") or ""),
                    1 if autopilot else 0,
                    now,
                    now,
                ),
            )
            return "inserted"

    def claim_next_ai(self, account: str) -> dict | None:
        now = time.time()
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """
                SELECT *
                  FROM tasks
                 WHERE account=?
                   AND stage='queued_ai'
                   AND next_attempt_at<=?
                 ORDER BY created_at
                 LIMIT 1
                """,
                (str(account), now),
            ).fetchone()
            if row is None:
                con.commit()
                return None

            changed = con.execute(
                """
                UPDATE tasks
                   SET stage='processing_ai',
                       status='AI обрабатывает',
                       updated_at=?
                 WHERE key=? AND stage='queued_ai'
                """,
                (now, row["key"]),
            ).rowcount
            if not changed:
                con.rollback()
                return None

            con.commit()
            data = dict(row)
            data["stage"] = "processing_ai"
            data["status"] = "AI обрабатывает"
            data["updated_at"] = now
            return data

    def requeue_ai(self, key: str, error: str = "") -> None:
        now = time.time()
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT attempts FROM tasks WHERE key=?",
                (str(key),),
            ).fetchone()
            attempts = int(row["attempts"] or 0) + 1 if row else 1
            delay = min(300, 15 * (2 ** min(attempts - 1, 4)))
            con.execute(
                """
                UPDATE tasks
                   SET stage='queued_ai',
                       status='Повтор AI',
                       attempts=?,
                       next_attempt_at=?,
                       updated_at=?,
                       last_error=?
                 WHERE key=?
                """,
                (attempts, now + delay, now, str(error)[:1000], str(key)),
            )

    def upsert_ready(self, item, *, autopilot: bool = False, status: str = "Ожидает") -> None:
        now = time.time()
        key = str(getattr(item, "key", "") or "")
        account = str(getattr(item, "account", "") or "")
        with self._lock, self._connect() as con:
            old = con.execute(
                "SELECT created_at, account FROM tasks WHERE key=?",
                (key,),
            ).fetchone()
            created_at = float(old["created_at"]) if old else now
            if not account and old:
                account = str(old["account"] or "")
            con.execute(
                """
                INSERT INTO tasks (
                    key, account, chat_id, message_id, chat_title,
                    post_text, comment, autopilot, stage, status,
                    attempts, next_attempt_at, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, 0, 0, ?, ?, '')
                ON CONFLICT(key) DO UPDATE SET
                    account=excluded.account,
                    chat_id=excluded.chat_id,
                    message_id=excluded.message_id,
                    chat_title=excluded.chat_title,
                    post_text=excluded.post_text,
                    comment=excluded.comment,
                    autopilot=excluded.autopilot,
                    stage='ready',
                    status=excluded.status,
                    next_attempt_at=0,
                    updated_at=excluded.updated_at,
                    last_error=''
                """,
                (
                    key,
                    account,
                    int(getattr(item, "chat_id", 0) or 0),
                    int(getattr(item, "message_id", 0) or 0),
                    str(getattr(item, "chat_title", "") or ""),
                    str(getattr(item, "post_text", "") or ""),
                    str(getattr(item, "comment", "") or ""),
                    1 if autopilot else 0,
                    str(status),
                    created_at,
                    now,
                ),
            )

    def set_status(self, key: str, status: str, *, clear_error: bool = False) -> None:
        with self._lock, self._connect() as con:
            if clear_error:
                con.execute(
                    "UPDATE tasks SET status=?, last_error='', updated_at=? WHERE key=?",
                    (str(status), time.time(), str(key)),
                )
            else:
                con.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE key=?",
                    (str(status), time.time(), str(key)),
                )

    def set_error(self, key: str, status: str, error: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE tasks SET status=?, last_error=?, updated_at=? WHERE key=?",
                (str(status), str(error)[:4000], time.time(), str(key)),
            )

    def remove(self, key: str) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM tasks WHERE key=?", (str(key),))

    def get(self, key: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM tasks WHERE key=?",
                (str(key),),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_ready(self, account: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE stage='ready'"
        args: tuple = ()
        if account is not None:
            sql += " AND account=?"
            args = (str(account),)
        sql += " ORDER BY created_at"
        with self._connect() as con:
            return [dict(row) for row in con.execute(sql, args).fetchall()]

    def stats(self) -> dict:
        result = {
            "queued_ai": 0,
            "processing_ai": 0,
            "ready": 0,
            "total": 0,
        }
        with self._connect() as con:
            for row in con.execute(
                "SELECT stage, COUNT(*) AS n FROM tasks GROUP BY stage"
            ).fetchall():
                stage = str(row["stage"])
                n = int(row["n"] or 0)
                result[stage] = n
                result["total"] += n
        return result

    def upsert_captcha(
        self,
        challenge_id: str,
        *,
        account: str,
        chat_id: int,
        message_id: int,
        chat_title: str = "",
        sender_id: int = 0,
        sender_name: str = "",
        sender_username: str = "",
        challenge_text: str = "",
        buttons: list | None = None,
        media_path: str = "",
        status: str = "Ожидает ответа",
    ) -> None:
        now = time.time()
        buttons_json = json.dumps(buttons or [], ensure_ascii=False)
        with self._lock, self._connect() as con:
            old = con.execute(
                "SELECT created_at FROM captcha_challenges WHERE id=?",
                (str(challenge_id),),
            ).fetchone()
            created_at = float(old["created_at"]) if old else now
            con.execute(
                """
                INSERT INTO captcha_challenges (
                    id, account, chat_id, message_id, chat_title,
                    sender_id, sender_name, sender_username,
                    challenge_text, buttons_json, media_path,
                    status, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    account=excluded.account,
                    chat_id=excluded.chat_id,
                    message_id=excluded.message_id,
                    chat_title=excluded.chat_title,
                    sender_id=excluded.sender_id,
                    sender_name=excluded.sender_name,
                    sender_username=excluded.sender_username,
                    challenge_text=excluded.challenge_text,
                    buttons_json=excluded.buttons_json,
                    media_path=excluded.media_path,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    str(challenge_id),
                    str(account),
                    int(chat_id or 0),
                    int(message_id or 0),
                    str(chat_title or ""),
                    int(sender_id or 0),
                    str(sender_name or ""),
                    str(sender_username or ""),
                    str(challenge_text or ""),
                    buttons_json,
                    str(media_path or ""),
                    str(status or "Ожидает ответа"),
                    created_at,
                    now,
                ),
            )

    def list_captchas(self, *, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM captcha_challenges"
        args: tuple = ()
        if active_only:
            sql += " WHERE status NOT IN ('Пройдена', 'Удалена')"
        sql += " ORDER BY created_at"
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["buttons"] = json.loads(item.pop("buttons_json", "[]") or "[]")
            except Exception:
                item["buttons"] = []
                item.pop("buttons_json", None)
            result.append(item)
        return result

    def get_captcha(self, challenge_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM captcha_challenges WHERE id=?",
                (str(challenge_id),),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["buttons"] = json.loads(item.pop("buttons_json", "[]") or "[]")
        except Exception:
            item["buttons"] = []
            item.pop("buttons_json", None)
        return item

    def set_captcha_status(
        self,
        challenge_id: str,
        status: str,
        *,
        error: str = "",
    ) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                UPDATE captcha_challenges
                   SET status=?, last_error=?, updated_at=?
                 WHERE id=?
                """,
                (
                    str(status),
                    str(error)[:4000],
                    time.time(),
                    str(challenge_id),
                ),
            )

    def remove_captcha(self, challenge_id: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "DELETE FROM captcha_challenges WHERE id=?",
                (str(challenge_id),),
            )

    def latest_pending_captcha(
        self,
        account: str,
        *,
        max_age_seconds: float = 60.0,
    ) -> dict | None:
        cutoff = time.time() - max(1.0, float(max_age_seconds))
        with self._connect() as con:
            row = con.execute(
                """
                SELECT *
                  FROM captcha_challenges
                 WHERE account=?
                   AND status NOT IN ('Пройдена', 'Удалена')
                   AND created_at>=?
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (str(account), cutoff),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["buttons"] = json.loads(item.pop("buttons_json", "[]") or "[]")
        except Exception:
            item["buttons"] = []
            item.pop("buttons_json", None)
        return item

    def resolve_recent_captcha(
        self,
        account: str,
        *,
        chat_id: int | None = None,
        max_age_seconds: float = 600.0,
    ) -> dict | None:
        cutoff = time.time() - max(1.0, float(max_age_seconds))
        sql = """
            SELECT *
              FROM captcha_challenges
             WHERE account=?
               AND status NOT IN ('Пройдена', 'Удалена')
               AND created_at>=?
        """
        args: list = [str(account), cutoff]
        if chat_id is not None:
            sql += " AND chat_id=?"
            args.append(int(chat_id))
        sql += " ORDER BY created_at DESC LIMIT 1"

        with self._lock, self._connect() as con:
            row = con.execute(sql, tuple(args)).fetchone()
            if row is None:
                return None
            con.execute(
                """
                UPDATE captcha_challenges
                   SET status='Пройдена', last_error='', updated_at=?
                 WHERE id=?
                """,
                (time.time(), str(row["id"])),
            )
        return dict(row)

    def has_active_captcha(self, account: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT 1
                  FROM captcha_challenges
                 WHERE account=?
                   AND status NOT IN ('Пройдена', 'Удалена')
                 LIMIT 1
                """,
                (str(account),),
            ).fetchone()
            return row is not None

    def resolve_captchas_for_chat(
        self,
        account: str,
        chat_id: int,
    ) -> list[dict]:
        """Resolve every active duplicate challenge for one account/chat."""
        now = time.time()
        with self._lock, self._connect() as con:
            rows = con.execute(
                """
                SELECT *
                  FROM captcha_challenges
                 WHERE account=?
                   AND chat_id=?
                   AND status NOT IN ('Пройдена', 'Удалена')
                 ORDER BY created_at
                """,
                (str(account), int(chat_id)),
            ).fetchall()
            if rows:
                con.execute(
                    """
                    UPDATE captcha_challenges
                       SET status='Пройдена', last_error='', updated_at=?
                     WHERE account=?
                       AND chat_id=?
                       AND status NOT IN ('Пройдена', 'Удалена')
                    """,
                    (now, str(account), int(chat_id)),
                )
            return [dict(row) for row in rows]


    def submit_command(self, action: str, key: str, comment: str | None = None) -> str:
        command_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO commands (
                    id, action, task_key, comment, state,
                    attempts, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, '')
                """,
                (
                    command_id,
                    str(action),
                    str(key),
                    None if comment is None else str(comment),
                    now,
                    now,
                ),
            )
        return command_id

    def claim_commands(self, limit: int = 50) -> list[dict]:
        now = time.time()
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # Recover a command if the engine crashed after claiming it.
            con.execute(
                """
                UPDATE commands
                   SET state='pending', updated_at=?
                 WHERE state='processing'
                   AND updated_at<?
                """,
                (now, now - 60),
            )
            rows = con.execute(
                """
                SELECT *
                  FROM commands
                 WHERE state='pending'
                 ORDER BY created_at
                 LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            for command_id in ids:
                con.execute(
                    "UPDATE commands SET state='processing', updated_at=? WHERE id=?",
                    (now, command_id),
                )
            con.commit()
            return [
                {
                    "id": str(row["id"]),
                    "action": str(row["action"]),
                    "key": str(row["task_key"]),
                    "comment": row["comment"],
                    "attempts": int(row["attempts"] or 0),
                }
                for row in rows
            ]

    def ack_command(self, command_id: str) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM commands WHERE id=?", (str(command_id),))

    def retry_command(self, command_id: str, error: str = "") -> None:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT attempts FROM commands WHERE id=?",
                (str(command_id),),
            ).fetchone()
            attempts = int(row["attempts"] or 0) + 1 if row else 1
            state = "failed" if attempts >= 3 else "pending"
            con.execute(
                """
                UPDATE commands
                   SET state=?,
                       attempts=?,
                       updated_at=?,
                       last_error=?
                 WHERE id=?
                """,
                (state, attempts, time.time(), str(error)[:1000], str(command_id)),
            )


def row_to_item(row: dict):
    return SimpleNamespace(
        key=str(row.get("key") or ""),
        account=str(row.get("account") or ""),
        chat_id=int(row.get("chat_id") or 0),
        message_id=int(row.get("message_id") or 0),
        chat_title=str(row.get("chat_title") or ""),
        post_text=str(row.get("post_text") or ""),
        comment=str(row.get("comment") or ""),
    )
