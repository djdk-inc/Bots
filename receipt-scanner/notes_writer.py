"""Write receipt records as JSON to an Apple Notes note called RECEIPTS.

Reads the existing note on each run, deduplicates by rowid, and rewrites it.
If the note doesn't exist it is created (in the default iCloud account).
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

NOTE_NAME = "RECEIPTS"
_TMP = "/tmp/receipt_bot_notes.html"


def _ensure_notes_running() -> None:
    result = subprocess.run(["pgrep", "-x", "Notes"], capture_output=True)
    if result.returncode != 0:
        subprocess.Popen(["open", "-a", "Notes"])
        time.sleep(3)


def _strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&amp;?", "&", text)
    text = re.sub(r"&lt;?", "<", text)
    text = re.sub(r"&gt;?", ">", text)
    text = re.sub(r"&quot;?", '"', text)
    return text.replace("&#39;", "'").replace("&nbsp;", " ").strip()


def _read_existing() -> tuple[list[dict], set[int]]:
    safe_note = NOTE_NAME.replace('"', '\\"')
    script = f"""
tell application "Notes"
    set matchingNotes to (notes whose name begins with "{safe_note}")
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
    escaped = json_text.replace("<", "&lt;").replace(">", "&gt;")
    lines = escaped.split("\n")
    html = f"<div>{NOTE_NAME}</div>" + "".join(
        f"<div>{line}</div>" if line.strip() else "<div><br></div>"
        for line in lines
    )
    with open(_TMP, "w", encoding="utf-8") as f:
        f.write(html)

    safe_note = NOTE_NAME.replace('"', '\\"')
    script = f"""
tell application "Notes"
    set note_content to read POSIX file "{_TMP}"
    set matchingNotes to (notes whose name begins with "{safe_note}")
    if length of matchingNotes is 0 then
        make new note with properties {{name: "{safe_note}", body: note_content}}
    else
        set body of item 1 of matchingNotes to note_content
    end if
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    try:
        os.unlink(_TMP)
    except FileNotFoundError:
        pass
    if result.returncode != 0:
        print(f"    Notes error: {result.stderr.strip()}")
    return result.returncode == 0


def archive_receipts(new_records: list[dict]) -> bool:
    """
    Merge new_records into the RECEIPTS note, deduplicating by rowid.
    Each record should have at minimum: rowid, date, sender, business, url.
    Returns True on success.
    """
    if not new_records:
        return True

    _ensure_notes_running()

    existing, existing_rowids = _read_existing()
    to_add = [r for r in new_records if r["rowid"] not in existing_rowids]
    if not to_add:
        return True

    now = datetime.now(timezone.utc).isoformat()
    stamped = [{**r, "archived_at": now} for r in to_add]
    return _write_note(existing + stamped)
