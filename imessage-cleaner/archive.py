#!/usr/bin/env python3
"""Archive deleted messages to a single Apple Note called TRANSCRIPTS."""

import subprocess
import time
from datetime import datetime

FOLDER = "iMessage Archive"
NOTE_NAME = "TRANSCRIPTS"


def _ensure_notes_running() -> None:
    result = subprocess.run(["pgrep", "-x", "Notes"], capture_output=True)
    if result.returncode != 0:
        subprocess.Popen(["open", "-a", "Notes"])
        time.sleep(3)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def archive_to_notes(messages: list[dict]) -> bool:
    if not messages:
        return True

    _ensure_notes_running()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = []
    for m in messages:
        text = m["text"].replace("<", "&lt;").replace(">", "&gt;")
        rows.append(
            f"<b>From:</b> {m['sender']}<br>"
            f"<b>Date:</b> {m['date']}<br>"
            f"<b>Type:</b> {m['category']}<br>"
            f"{text}<br>"
            f"————————————————<br>"
        )

    entry = (
        f"<b>▶ {now}  ({len(messages)} messages deleted)</b><br><br>"
        + "".join(rows)
        + "<br>"
    )

    safe_folder = _escape(FOLDER)
    safe_note = _escape(NOTE_NAME)
    safe_entry = _escape(entry)

    script = f"""
tell application "Notes"
    -- ensure folder exists
    if not (exists folder "{safe_folder}") then
        make new folder with properties {{name: "{safe_folder}"}}
    end if
    set f to folder "{safe_folder}"

    -- find or create the TRANSCRIPTS note
    set matchingNotes to (notes of f whose name is "{safe_note}")
    if length of matchingNotes is 0 then
        make new note at f with properties {{name: "{safe_note}", body: "{safe_entry}"}}
    else
        set theNote to item 1 of matchingNotes
        set body of theNote to (body of theNote) & "{safe_entry}"
    end if
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0
