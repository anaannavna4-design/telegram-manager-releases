# NeuroComment 1.2.0 RC2

RC2 builds on RC1 and adds detailed Telegram publication diagnostics.

## New

When a comment cannot be published, NeuroComment now stores the original Telegram error in SQLite instead of showing only "Ограничение Telegram".

The Comments page has a new button:

`⚠ Подробнее об ошибке`

For a selected failed comment it shows:

- publication stage
- Telethon exception class
- original Telegram message
- RPC code
- FloodWait / SlowMode wait seconds when available
- request type
- auto-leave result for permanent write/banned restrictions

The same details are written to Logs.

Examples include:

`FloodWaitError`
`SlowModeWaitError`
`PeerFloodError`
`UserBannedInChannelError`
`ChatWriteForbiddenError`
`UserRestrictedError`

## Important

Errors that happened before RC2 only contain the old generic status. Their original Telegram exception cannot be reconstructed. Retry publication after installing RC2 to capture the detailed error.

## Files to copy over RC1

- `main.py`
- `desktop_app.py`
- `desktop_bridge.py`
- `queue_store.py`
- `publisher.py`

Do not delete `%LOCALAPPDATA%\NeuroComment`.
