# iMessage Cleaner

A macOS-only tool that scans your iMessage database for phishing texts, job scams, OTP codes, and ads — then archives, blocks, and deletes them.

## What it does

Messages are classified into four categories using regex pattern matching:

| Category | Icon | Auto-delete |
|---|---|---|
| OTP / Verification Codes | 🔑 | No |
| Phishing / Scams | 🚨 | Yes |
| Job Scams | 💼 | Yes |
| Advertisements | 📢 | Yes |
| Legitimate | ✅ | No |

## Two modes

### Interactive (`imessage_cleaner.py`)

Run manually to review and approve deletions:

```bash
python3 imessage_cleaner.py              # scan last 7 days
python3 imessage_cleaner.py --days 30    # scan last 30 days
python3 imessage_cleaner.py --all        # full history
python3 imessage_cleaner.py --dry-run    # show summary only, no prompt
python3 imessage_cleaner.py --notify     # macOS notification with flagged count (cron-friendly)
```

### Automated (`bot.py`)

Runs without prompts — archive → block → delete in one shot. Designed for cron or launchd:

```bash
python3 bot.py
```

The bot:
1. Scans the last `SCAN_DAYS` days (set in `config.py`)
2. Archives all flagged messages to an Apple Note called **TRANSCRIPTS** in an **iMessage Archive** folder
3. Blocks sender phone numbers via the macOS block list (`com.apple.cmfsyncagent`)
4. Deletes the messages from `chat.db`, with a timestamped backup first

## Requirements

- macOS (uses `chat.db`, AppleScript, and the cmfsyncagent plist)
- Python 3.10+ (stdlib only — no pip installs)
- **Full Disk Access** granted to Terminal (or whichever app runs the script) in System Settings → Privacy & Security

## Setup

```bash
cp config.example.py config.py
# edit config.py with your settings
```

### Run daily via cron

```
0 8 * * * /usr/bin/python3 /path/to/imessage-cleaner/bot.py >> /tmp/imessage_cleaner.log 2>&1
```

## How deletions work

Before any delete, the script:
1. Creates a timestamped backup of `chat.db` in `~/.imessage_cleaner_backups/`
2. Drops and recreates the `after_delete_on_message_plugin` trigger (which references an internal Messages.app function) so the delete doesn't crash

Quit and reopen Messages.app after running to see changes reflected.
