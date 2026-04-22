#!/usr/bin/env python3
"""Archive deleted messages to Apple Notes before removal."""

import subprocess
import time
from datetime import datetime

FOLDER = "iMessage Archive"


def _ensure_notes_running() -> None:
    result = subprocess.run(
        ["pgrep", "-x", "Notes"], capture_output=True
    )
    if result.returncode != 0:
        subprocess.Popen(["open", "-a", "Notes"])
        time.sleep(3)


def archive_to_notes(messages: list[dict]) -> bool:
    if not messages:
        return True

    _ensure_notes_running()
    now = datetime.now()
    title = f"Deleted iMessages — {now.strftime('%Y-%m-%d %H:%M')}"

    rows = []
    for m in messages:
        rows.append(
            f"<b>{m['sender']}</b>  ·  {m['date']}  ·  {m['category']}<br>"
            f"{m['text'].replace('<', '&lt;').replace('>', '&gt;')}<br><br>"
        )

    body_html = (
        f"<b>Archived:</b> {now.strftime('%Y-%m-%d %H:%M')}<br>"
        f"<b>Count:</b> {len(messages)}<br><br>"
        f"{''.join(rows)}"
    )

    # Escape for AppleScript string literal
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_body = body_html.replace("\\", "\\\\").replace('"', '\\"')

    script = f"""
tell application "Notes"
    if not (exists folder "{FOLDER}") then
        make new folder with properties {{name: "{FOLDER}"}}
    end if
    make new note at folder "{FOLDER}" with properties {{name: "{safe_title}", body: "{safe_body}"}}
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0
