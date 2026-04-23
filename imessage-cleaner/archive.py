#!/usr/bin/env python3
"""Archive deleted messages as JSON to an Apple Note called TRANSCRIPTS."""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

FOLDER = "iMessage Archive"
NOTE_NAME = "TRANSCRIPTS"
_TMP = "/tmp/imessage_transcript.json"


def _ensure_notes_running() -> None:
    result = subprocess.run(["pgrep", "-x", "Notes"], capture_output=True)
    if result.returncode != 0:
        subprocess.Popen(["open", "-a", "Notes"])
        time.sleep(3)


def _escape_as(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Notes omits trailing semicolons on entities (&amp not &amp;) — handle both.
    text = re.sub(r"&amp;?", "&", text)
    text = re.sub(r"&lt;?", "<", text)
    text = re.sub(r"&gt;?", ">", text)
    text = re.sub(r"&quot;?", '"', text)
    return text.replace("&#39;", "'").replace("&nbsp;", " ").strip()


def _read_existing() -> tuple[list[dict], set[int]]:
    safe_folder = _escape_as(FOLDER)
    safe_note = _escape_as(NOTE_NAME)
    script = f"""
tell application "Notes"
    if not (exists folder "{safe_folder}") then return ""
    set f to folder "{safe_folder}"
    set matchingNotes to (notes of f whose name begins with "{safe_note}")
    if length of matchingNotes is 0 then return ""
    return body of item 1 of matchingNotes
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return [], set()

    raw = _strip_html(result.stdout.strip())
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return [], set()
    try:
        records = json.loads(match.group())
        rowids = {r["rowid"] for r in records if "rowid" in r}
        return records, rowids
    except (json.JSONDecodeError, TypeError):
        return [], set()


def _write_note(records: list[dict]) -> bool:
    json_text = json.dumps(records, indent=2, ensure_ascii=False)
    # Escape < > so message bodies can't inject HTML tags; leave & raw —
    # Notes encodes & as &amp (no semicolon) and _strip_html decodes it on read.
    escaped = json_text.replace("<", "&lt;").replace(">", "&gt;")
    lines = escaped.split("\n")
    html = f"<div>{NOTE_NAME}</div>" + "".join(
        f"<div>{line}</div>" if line.strip() else "<div><br></div>"
        for line in lines
    )
    with open(_TMP, "w", encoding="utf-8") as f:
        f.write(html)

    safe_folder = _escape_as(FOLDER)
    safe_note = _escape_as(NOTE_NAME)
    script = f"""
tell application "Notes"
    set json_content to read POSIX file "{_TMP}"
    if not (exists folder "{safe_folder}") then
        make new folder with properties {{name: "{safe_folder}"}}
    end if
    set f to folder "{safe_folder}"
    set matchingNotes to (notes of f whose name begins with "{safe_note}")
    if length of matchingNotes is 0 then
        make new note at f with properties {{name: "{safe_note}", body: json_content}}
    else
        set body of item 1 of matchingNotes to json_content
    end if
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    try:
        os.unlink(_TMP)
    except FileNotFoundError:
        pass
    return result.returncode == 0


def archive_to_notes(messages: list[dict]) -> bool:
    if not messages:
        return True

    _ensure_notes_running()

    existing, existing_rowids = _read_existing()
    new_messages = [m for m in messages if m["rowid"] not in existing_rowids]
    if not new_messages:
        return True

    now = datetime.now(timezone.utc).isoformat()
    new_records = [
        {
            "rowid": m["rowid"],
            "archived_at": now,
            "date": m["date"],
            "sender": m["sender"],
            "category": m["category"],
            "text": m["text"],
        }
        for m in new_messages
    ]

    return _write_note(existing + new_records)
