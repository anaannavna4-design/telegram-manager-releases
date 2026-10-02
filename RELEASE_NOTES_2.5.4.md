# Telegram Manager 2.5.4

## Fixed

- Channels with a pending administrator approval request are no longer retried on subsequent runs.
- The Remaining counter now represents only channels that still need processing:
  `Total - Subscribed - Waiting for approval`.
- Existing pending join requests remain saved after application restart.

## New

- Joining and leaving channels can now run simultaneously on different Telegram accounts in the same Telegram Manager process.
- An account already used by the unsubscribe workflow is skipped by the subscription engine.
- An account already used by the subscription workflow is safely detached before unsubscribe starts.
- One Telegram account is never allowed to join and leave channels at the same time.

## Compatibility

- Existing Telegram accounts are preserved.
- Existing Telegram session files are preserved.
- Channel groups and saved progress are preserved.
- Existing license activation is preserved.

## Installation

Install the new version over the existing installation.

Do not delete the Telegram Manager application data folder during the update.

## Verification

Use `SHA256SUMS.txt` from the release package to verify the installer.

## Important

Telegram Manager is independent software and is not affiliated with, endorsed by, or sponsored by Telegram.

Only use Telegram accounts that you own or are authorized to operate.
