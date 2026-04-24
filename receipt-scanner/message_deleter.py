"""Delete processed iMessage rows from chat.db.

Backs up the DB before touching it. Drops and recreates the
after_delete_on_message_plugin trigger, which calls a function that only
exists inside Messages.app — the same pattern used by imessage-cleaner.
"""
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / "Library/Messages/chat.db"
BACKUP_DIR = Path.home() / ".receipt_bot_db_backups"


def delete_messages(rowids: list[int]) -> int:
    """
    Delete message rows by rowid. Returns count deleted.
    Backs up chat.db to ~/.receipt_bot_db_backups/ first.
    """
    if not rowids:
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"chat_{stamp}.db"
    shutil.copy2(DB_PATH, backup)
    print(f"    DB backed up → {backup}")

    conn = sqlite3.connect(DB_PATH)
    try:
        # The after_delete_on_message_plugin trigger calls a C function that only
        # exists inside Messages.app. Drop it, do the delete, then recreate it so
        # Messages.app doesn't break when it opens the DB next.
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'after_delete_on_message_plugin'"
        ).fetchone()
        trigger_sql = row[0] if row else None

        if trigger_sql:
            conn.execute("DROP TRIGGER IF EXISTS after_delete_on_message_plugin")

        ph = ",".join("?" * len(rowids))
        cur = conn.execute(f"DELETE FROM message WHERE ROWID IN ({ph})", rowids)
        deleted = cur.rowcount

        if trigger_sql:
            conn.execute(trigger_sql)

        conn.commit()
    finally:
        conn.close()

    _restart_messages()
    return deleted


def _restart_messages():
    """Quit and reopen Messages so it reloads from the updated DB."""
    subprocess.run(["osascript", "-e", 'quit app "Messages"'], capture_output=True)
    time.sleep(2)
    subprocess.run(["open", "-a", "Messages"], capture_output=True)
