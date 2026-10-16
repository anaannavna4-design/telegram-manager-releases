# NeuroComment 1.2.0 RC3 — Manual Telegram verification inbox

RC3 builds on RC2.

## What is new

NeuroComment can now mirror supported Telegram anti-spam verification challenges into the desktop app, so the user does not need to open the Telegram client.

This is a **manual verification relay**:
- NeuroComment does not solve a challenge automatically.
- The user sees the challenge in the desktop app.
- The user types the answer or clicks one of the mirrored Telegram buttons.
- NeuroComment sends that exact user-selected response through the already-authorized Telethon session.

## New tab: Проверки

The tab shows:
- Telegram account
- chat/group
- guard bot
- status
- challenge text
- downloaded image/media when available
- inline callback buttons
- text answer field

Actions:
- Отправить ответ
- click a mirrored Telegram callback button
- open URL buttons in the default browser
- Проверка пройдена
- Удалить

## Comment behavior

If joining a discussion group triggers a verification challenge:
- the comment is kept in SQLite;
- status becomes **Требуется CAPTCHA**;
- it is not discarded as a permanent Telegram restriction.

After a verification is confirmed:
- autopilot accounts retry their waiting comments automatically;
- manual accounts change to **CAPTCHA пройдена — готов к публикации** and still require the user's publish action.

## Persistence

Challenges are stored in the existing:

`%LOCALAPPDATA%\NeuroComment\neurocomment_queue.sqlite3`

Downloaded verification media is stored under:

`%LOCALAPPDATA%\NeuroComment\captcha_media\<account>\`

## Files changed / added

- `main.py`
- `desktop_app.py`
- `desktop_bridge.py`
- `queue_store.py`
- `publisher.py`
- `captcha_manager.py`

Keep all other files from the latest working full NeuroComment build.

## Notes

Detection is deliberately conservative and focuses on bot-originated verification messages with strong anti-spam/verification signals. A provider can change its message format at any time, so real Defensy examples should be tested before final 1.2.0 release.
