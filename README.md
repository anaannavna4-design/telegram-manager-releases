# Telegram Manager

Official downloads and release files for **Telegram Manager for Windows**.

## Current version

**Telegram Manager 2.5.2**

### What's new in 2.5.2

- Fixed a bug where channel unsubscribe could hang indefinitely on one Telegram channel.
- Added a 45-second timeout for unsubscribe Telegram requests.
- Added one automatic retry after a timeout.
- If the retry also times out, the channel is recorded as an error and processing continues with the next channel.
- Stopping an unsubscribe task can now interrupt a stuck Telegram request.
- Existing unsubscribe progress is preserved and can be resumed after restart.
- Parallel unsubscribe from multiple accounts and independent channel groups from 2.5.1 are retained.

## Download

Public customer builds are published in the **Releases** section of this repository.

Permanent latest-download link:

`https://github.com/anaannavna4-design/telegram-manager-releases/releases/latest/download/TelegramManager-Setup.exe`

## System requirements

- Windows 10 or Windows 11
- 64-bit system
- Internet connection
- A valid Telegram Manager license

## Activation

After purchase, you receive a unique registration code.  
Enter that code in Telegram Manager when the activation window appears.

A license may be limited to a specific number of devices depending on the purchased edition.

## Updates

New versions are published through GitHub Releases.  
The permanent latest-download link above can stay the same between versions.

Before important upgrades, creating a backup is recommended.

## Security

For each public release, a SHA-256 checksum file is provided so that the installer can be verified after download.

Do not share Telegram session files, API credentials, login codes or 2FA passwords.

## Disclaimer

Telegram Manager is independent software. It is not affiliated with, endorsed by, or sponsored by Telegram.

Only use Telegram accounts that you own or are authorized to operate.
