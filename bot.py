#!/usr/bin/env python3
"""Daily iMessage cleanup bot.

Cron runs once at 9am:
  1. Check if yesterday's summary was approved (YES reply) → delete if so
  2. Scan today's messages → send new summary if anything flagged
"""

import json
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from imessage_cleaner import fetch_messages, delete_from_db, CATEGORIES, DB_PATH
import config

APPLE_EPOCH = 978307200
STATE_DIR = Path.home() / ".imessage_cleaner"
STATE_FILE = STATE_DIR / "pending.json"
DELETABLE = [c for c, (_, _, default) in CATEGORIES.items() if default]

YES_WORDS = {"yes", "y", "ok", "approve", "yep", "yeah", "sure"}


def send_imessage(text: str) -> None:
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "Messages"\n'
        f'  set s to 1st service whose service type = iMessage\n'
        f'  set b to buddy "{config.PHONE}" of s\n'
        f'  send "{safe}" to b\n'
        f'end tell'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)


def find_chat_id(phone: str) -> int | None:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    row = conn.execute("""
        SELECT chat.ROWID
        FROM chat
        JOIN chat_handle_join ON chat.ROWID = chat_handle_join.chat_id
        JOIN handle ON chat_handle_join.handle_id = handle.ROWID
        WHERE handle.id = ?
        ORDER BY chat.last_read_message_timestamp DESC
        LIMIT 1
    """, (phone,)).fetchone()
    conn.close()
    return row[0] if row else None


def check_yes_reply(chat_id: int, after_unix: float) -> bool:
    after_apple_ns = (after_unix - APPLE_EPOCH) * 1_000_000_000
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = conn.execute("""
        SELECT message.text
        FROM message
        JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        WHERE chat_message_join.chat_id = ?
          AND message.date > ?
          AND message.is_from_me = 0
    """, (chat_id, after_apple_ns)).fetchall()
    conn.close()
    return any(
        (r[0] or "").strip().lower() in YES_WORDS
        for r in rows
    )


def build_summary(grouped: dict) -> str:
    today = date.today().strftime("%b %d")
    lines = [f"iMessage Cleaner — {today}", ""]
    total = 0
    for cat in DELETABLE:
        if cat in grouped:
            label, icon, _ = CATEGORIES[cat]
            n = len(grouped[cat])
            total += n
            lines.append(f"{icon} {label}: {n}")
    lines += ["", f"Total: {total} messages", "", "Reply YES to delete."]
    return "\n".join(lines)


def load_state() -> dict | None:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None


def save_state(rowids: list[int], chat_id: int) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "date": date.today().isoformat(),
        "sent_at": datetime.now().timestamp(),
        "chat_id": chat_id,
        "rowids": rowids,
    }))


def main() -> None:
    state = load_state()

    # Step 1: check if yesterday's scan was approved
    if state:
        if check_yes_reply(state["chat_id"], state["sent_at"]):
            deleted = delete_from_db(state["rowids"])
            send_imessage(f"✓ Deleted {deleted} messages.")
        STATE_FILE.unlink()

    # Step 2: scan today
    messages = fetch_messages(days=config.SCAN_DAYS)
    grouped: dict = {}
    for m in messages:
        grouped.setdefault(m["category"], []).append(m)

    candidates = {c: grouped[c] for c in DELETABLE if c in grouped}
    if not candidates:
        return

    # Step 3: send summary
    send_imessage(build_summary(grouped))

    # Step 4: record what's pending approval
    time.sleep(2)
    chat_id = find_chat_id(config.PHONE)
    if not chat_id:
        return

    rowids = [m["rowid"] for msgs in candidates.values() for m in msgs]
    save_state(rowids, chat_id)


if __name__ == "__main__":
    main()
