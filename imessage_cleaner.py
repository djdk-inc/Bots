#!/usr/bin/env python3
"""iMessage cleaner — scans for OTP codes, phishing, and ads; reviews and deletes on approval."""

import sqlite3
import shutil
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / "Library/Messages/chat.db"
BACKUP_DIR = Path.home() / ".imessage_cleaner_backups"

# ── Classification patterns ──────────────────────────────────────────────────

OTP_PATTERNS = [
    r'(?:your\s+)?(?:code|otp|pin|passcode|one.time\s+password)\s*(?:is\s*:?|:)\s*\d{4,8}',
    r'\d{4,8}\s+is\s+your\s+(?:\w+\s+)?(?:verification|confirmation|login|security|access)\s+code',
    r'verification\s+code[:\s]+\d{4,8}',
    r'use\s+\d{4,8}\s+to\s+(?:verify|confirm|login|sign)',
    r'(?:code|otp)\s*[:=]\s*\d{4,8}',
    r'\b\d{6}\b.{0,60}(?:don.t\s+share|never\s+share|do\s+not\s+share)',
    r'your\s+code\s+is\s*:?\s*\d{4,8}',
]

PHISHING_PATTERNS = [
    r'(?:suspend|suspended|suspension).{0,50}(?:account|license|driving|registration|privilege)',
    r'(?:outstanding|unpaid|overdue|delinquent).{0,50}(?:ticket|fine|penalty|balance)',
    r'(?:pay\s+now|immediate|urgent|action\s+required).{0,80}https?://',
    r'https?://[^\s]{0,30}(?:\.xyz|\.top|\.cfd|\.monster|\.icu|\.pw)',
    r'(?:coinbase|paypal|wells\s+fargo|bank\s+of\s+america).{0,50}(?:suspicious|unauthori|new\s+login|compromis)',
    r'(?:your\s+)?(?:account|card|wallet).{0,40}(?:has\s+been|will\s+be).{0,30}(?:block|suspend|close|compromis)',
    r'reply\s*["\']?y["\']?.{0,40}(?:link|http|www\.)',
    r'rmv|motor\s+vehicle.{0,80}(?:suspension|overdue|penalty)',
]

JOB_SCAM_PATTERNS = [
    r'6\d\s*[–\-–—]\s*90\s*minutes?\s*(?:per|a|/)\s*day',
    r'60\s*(?:to|[-–—])\s*90\s*minutes?\s*(?:per|a|/)\s*day',
    r'\$\s*\d{2,4}\s*(?:to|[-–—])\s*\$?\s*\d{3,5}\s*(?:per|a|/)\s*day',
    r'\d{3,4}\s*(?:to|[-–—])\s*\d{3,5}\s*(?:per|a|/)\s*day',  # handles no $ sign
    r'(?:temu|amazon|walmart|costco|shein)\s*merchant',
    r'whatsapp[:\s]+\+\d{8,}',
    r'whatsapp.*\+1\d{10}',
    r'(?:base\s+salary|basic\s+salary)\s*(?:is|:)\s*\$?\s*\d{3,5}',
    r'(?:newbie|sign.?on|joining)\s+bonus',
    r'product\s+(?:review|listing|visibility).{0,80}(?:earn|salary|income|commission)',
    r'(?:adecco|linkedin|amazon|dsl|dripshop|fxpro|swagbucks).{0,80}(?:recruiter|recruitment|hr|staffing)',
    r'(?:paid\s+)?annual\s+leave.{0,60}(?:15|20|25)\s+days',
    r'(?:remote|part.?time|flexible).{0,80}(?:\$\d{3,}|daily\s+(?:pay|wage|earn)|earn\s+extra)',
    r'boost\s+(?:product|market|visibility)',
    r'(?:18|20|25|50)\s+openings?\s+(?:currently|available)',
    r'text\s+\+?1?\d{10}\s+(?:for|to)',
]

AD_PATTERNS = [
    r'(?:sign\s+up|subscribe|upgrade)\s+(?:for|to)\s+(?:premium|pro|plus|beta)',
    r'(?:truth\s+social|truth\+|truthsocial)',
    r'stop\s+to\s+(?:end|unsubscribe|opt.?out)',
    r'(?:fast\s+streaming|premium\s+features).{0,60}waiting',
]

CATEGORIES = {
    'otp':       ('OTP / Verification Codes',  '🔑', False),  # (label, icon, delete_by_default)
    'phishing':  ('Phishing / Scams',           '🚨', True),
    'job_scam':  ('Job Scams',                  '💼', True),
    'ad':        ('Advertisements',             '📢', True),
    'legitimate':('Legitimate',                 '✅', False),
}


def classify(text: str) -> str:
    t = text.lower()
    for p in OTP_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            return 'otp'
    for p in JOB_SCAM_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            return 'job_scam'
    for p in PHISHING_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            return 'phishing'
    for p in AD_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            return 'ad'
    return 'legitimate'


# ── Database ──────────────────────────────────────────────────────────────────

def fetch_messages(days: int) -> list[dict]:
    # Apple's CoreData epoch: 2001-01-01 00:00:00 UTC = unix 978307200
    apple_epoch = 978307200
    cutoff_apple = (datetime.now() - timedelta(days=days)).timestamp() - apple_epoch

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            message.ROWID                                                         AS rowid,
            datetime(message.date/1000000000 + 978307200, 'unixepoch', 'localtime') AS date,
            COALESCE(handle.id, 'Me')                                             AS sender,
            message.text
        FROM message
        LEFT JOIN handle ON message.handle_id = handle.ROWID
        WHERE message.text IS NOT NULL
          AND message.date / 1000000000 > ?
        ORDER BY message.date DESC
    """, (cutoff_apple,)).fetchall()
    conn.close()

    result = []
    for r in rows:
        cat = classify(r['text'])
        result.append({'rowid': r['rowid'], 'date': r['date'],
                       'sender': r['sender'], 'text': r['text'], 'category': cat})
    return result


def delete_from_db(rowids: list[int]) -> int:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = BACKUP_DIR / f"chat_{stamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    print(f"  Backup saved → {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    ph = ','.join('?' * len(rowids))
    cur = conn.execute(f"DELETE FROM message WHERE ROWID IN ({ph})", rowids)
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


# ── Display ───────────────────────────────────────────────────────────────────

def trunc(text: str, n: int = 90) -> str:
    text = text.replace('\n', ' ').strip()
    return text[:n] + '…' if len(text) > n else text


def print_summary(grouped: dict) -> None:
    total = sum(len(v) for v in grouped.values())
    print(f"\n{'━'*64}")
    print(f"  iMessage Cleaner  ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'━'*64}")
    print(f"  Messages scanned: {total}\n")

    for cat, (label, icon, _) in CATEGORIES.items():
        msgs = grouped.get(cat, [])
        if not msgs:
            continue
        print(f"  {icon}  {label}  ({len(msgs)})")
        print(f"  {'─'*58}")
        for m in msgs[:6]:
            sender = m['sender'][:35]
            print(f"  {m['date']}  {sender}")
            print(f"    {trunc(m['text'])}")
        if len(msgs) > 6:
            print(f"    … and {len(msgs) - 6} more")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description='Scan iMessages for spam/OTPs and optionally delete them.')
    parser.add_argument('--days', type=int, default=7,
                        help='How many days back to scan (default: 7)')
    parser.add_argument('--all', action='store_true',
                        help='Scan entire message history')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show summary without prompting for deletion')
    parser.add_argument('--notify', action='store_true',
                        help='Dry-run + send a macOS notification with the count (for cron use)')
    args = parser.parse_args()

    days = 36500 if args.all else args.days

    print(f"Scanning last {'all' if args.all else str(days) + ' day(s)'} of iMessages…")
    messages = fetch_messages(days=days)

    if not messages:
        print("No messages found in that window.")
        return

    grouped: dict[str, list] = {}
    for m in messages:
        grouped.setdefault(m['category'], []).append(m)

    if args.notify:
        deletable_cats = [c for c, (_, _, default) in CATEGORIES.items() if default]
        flagged = sum(len(grouped.get(c, [])) for c in deletable_cats)
        if flagged:
            subtitle = ', '.join(
                f"{CATEGORIES[c][1]}{len(grouped[c])}"
                for c in deletable_cats if c in grouped
            )
            subprocess.run([
                'osascript', '-e',
                f'display notification "{subtitle}" with title "iMessage Cleaner" '
                f'subtitle "{flagged} messages flagged — run imclean to review"'
            ], capture_output=True)
        return

    print_summary(grouped)

    if args.dry_run:
        return

    # Build deletion candidates from default-delete categories
    deletable_cats = [c for c, (_, _, default) in CATEGORIES.items() if default]
    candidates: dict[str, list] = {c: grouped[c] for c in deletable_cats if c in grouped}

    if not candidates:
        print("Nothing flagged for deletion. All clean!")
        return

    print(f"{'━'*64}")
    total_to_delete = sum(len(v) for v in candidates.values())
    cats_listed = ', '.join(
        f"{CATEGORIES[c][1]} {CATEGORIES[c][0]} ({len(v)})"
        for c, v in candidates.items()
    )
    print(f"  Marked for deletion: {total_to_delete} messages")
    print(f"  {cats_listed}")
    print(f"  OTP codes and legitimate messages will be kept.")
    print(f"{'━'*64}\n")

    answer = input("  Delete these messages? [y/N] ").strip().lower()
    if answer != 'y':
        print("  Cancelled — no messages deleted.")
        return

    rowids = [m['rowid'] for msgs in candidates.values() for m in msgs]
    print()
    deleted = delete_from_db(rowids)
    print(f"  Deleted {deleted} messages.")
    print()
    print("  Tip: quit and reopen Messages.app to see changes reflected.")


if __name__ == '__main__':
    main()
