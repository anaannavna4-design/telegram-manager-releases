# Telegram Manager 2.5.2

## Fixed

- Fixed an unsubscribe worker issue where a single Telegram channel could keep an account stuck in the "Working" state indefinitely.
- Added a 45-second timeout around Telegram unsubscribe requests.
- Added one automatic retry when a request times out.
- If the second attempt also times out, the channel is counted as an error and the worker automatically continues with the next channel.
- The Stop action can now interrupt an unsubscribe request that is currently stuck.

## Preserved behavior

- Existing unsubscribe progress is not reset by this update.
- Saved unsubscribe tasks can be resumed after restart.
- Multiple accounts can still unsubscribe in parallel.
- Each account can still use its own selected channel group.
- Telegram FloodWait handling remains automatic and independent per account.

## Installation

Download `TelegramManager-Setup.exe` from this release and run it over the existing installation.

Application data is stored separately from the installed program. Do not delete the Telegram Manager data folder when updating.

## Verification

Use `SHA256SUMS.txt` to verify the downloaded installer.

## Important

Telegram Manager is independent software and is not affiliated with, endorsed by, or sponsored by Telegram.

Only use Telegram accounts that you own or are authorized to operate.
