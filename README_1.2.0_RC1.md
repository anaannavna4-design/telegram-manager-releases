# NeuroComment 1.2.0 RC1 — Persistent Queue

Release-candidate patch on top of NeuroComment 1.1.4.

## What changed

- Replaced volatile `asyncio.Queue(maxsize=75)` with a durable SQLite queue.
- Backfill no longer drops/defer-loops a post just because the old in-memory queue is full.
- When the durable queue reaches its configured disk limit, Backfill applies backpressure and waits for capacity.
- AI tasks survive engine/app/Windows restarts.
- Ready comments survive restarts and remain visible even while the engine is stopped.
- Desktop commands are stored in SQLite and acknowledged after processing.
- Old `desktop_queue.json` is imported once into SQLite on upgrade.
- Removed the `desktop_queue.json.tmp -> desktop_queue.json` write path that caused WinError 5.
- Added queue counters in the Comments page:
  - waiting for AI
  - processing
  - ready
  - total
- Added setting: **Максимум задач в постоянной очереди** (default: 2000).
- Restored local autopilot jobs are rescheduled after restart when Telegram BotPanel is unavailable.

## New runtime file

User data remains in the existing permanent NeuroComment data directory.

The new queue database is:

`%LOCALAPPDATA%\NeuroComment\neurocomment_queue.sqlite3`

SQLite creates `-wal` and `-shm` side files while the app is running. This is normal.

## Upgrade from 1.1.x

This branch is a patch set, not a standalone full-source package.

Copy these files over the latest working full NeuroComment folder:

- `main.py`
- `desktop_app.py`
- `config.py`
- `settings_store.py`
- `desktop_bridge.py`
- `queue_store.py`

Keep the remaining files from the current working version, including:

- `app_paths.py`
- `storage.py`
- `telegram_monitor.py`
- `publisher.py`
- `bot_panel.py`
- `ai.py`

Do not delete `%LOCALAPPDATA%\NeuroComment`.

## First start

On the first engine start RC1 will try to import the old:

`desktop_queue.json`

into SQLite. If items are imported successfully, the old file is renamed to:

`desktop_queue.migrated.json`

## Queue behavior

Default durable task limit: **2000**.

When it is reached, logs look like:

`[BACKPRESSURE] Постоянная очередь заполнена ... Backfill приостановлен`

This means tasks are not being discarded. Backfill waits until the user/AI frees capacity, then resumes.

## Test checklist before final 1.2.0

1. Start with 100+ monitored channels and confirm the queue goes beyond the old limit of 75.
2. Stop NeuroComment while AI tasks are waiting; restart and confirm processing resumes.
3. Leave ready comments, restart, and confirm they remain in Comments.
4. Publish/skip/regenerate a restored comment.
5. Force-close the engine during AI generation and confirm the item returns to waiting state.
6. Verify no new `desktop_queue.json.tmp` WinError 5 occurs.
7. Test both Ollama and OpenAI providers.
8. Test manual and autopilot accounts separately.

No third-party dependency was added: Python's built-in `sqlite3` module is used.
