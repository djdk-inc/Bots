"""Write DELIVERY REPORT and DELIVERY TRANSCRIPTS to Apple Notes."""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

FOLDER = 'iMessage Archive'
REPORT_NOTE = 'DELIVERY REPORT'
TRANSCRIPTS_NOTE = 'DELIVERY TRANSCRIPTS'
_TMP = '/tmp/delivery_note.html'

_STATUS_ICONS = {
    'out_for_delivery': '📦',
    'delivered':        '✅',
    'in_transit':       '🚚',
    'attempted':        '⚠️',
    'exception':        '🚨',
    'unknown':          '❓',
}

_STATUS_LABELS = {
    'out_for_delivery': 'OUT FOR DELIVERY',
    'delivered':        'DELIVERED',
    'in_transit':       'IN TRANSIT',
    'attempted':        'NEEDS ATTENTION — Attempted',
    'exception':        'NEEDS ATTENTION — Exception',
    'unknown':          'OTHER',
}

_STATUS_ORDER = ['out_for_delivery', 'delivered', 'attempted', 'exception', 'in_transit', 'unknown']


def _ensure_notes_running() -> None:
    result = subprocess.run(['pgrep', '-x', 'Notes'], capture_output=True)
    if result.returncode != 0:
        subprocess.Popen(['open', '-a', 'Notes'])
        time.sleep(3)


def _escape_as(s: str) -> str:
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _strip_html(html: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&amp;?', '&', text)
    text = re.sub(r'&lt;?', '<', text)
    text = re.sub(r'&gt;?', '>', text)
    text = re.sub(r'&quot;?', '"', text)
    return text.replace('&#39;', "'").replace('&nbsp;', ' ').strip()


def _write_note(name: str, html_content: str) -> bool:
    with open(_TMP, 'w', encoding='utf-8') as f:
        f.write(html_content)

    safe_folder = _escape_as(FOLDER)
    safe_name = _escape_as(name)
    script = f"""
tell application "Notes"
    set note_content to read POSIX file "{_TMP}"
    if not (exists folder "{safe_folder}") then
        make new folder with properties {{name: "{safe_folder}"}}
    end if
    set f to folder "{safe_folder}"
    set matching to (notes of f whose name begins with "{safe_name}")
    if length of matching is 0 then
        make new note at f with properties {{name: "{safe_name}", body: note_content}}
    else
        set body of item 1 of matching to note_content
    end if
end tell
"""
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    try:
        os.unlink(_TMP)
    except FileNotFoundError:
        pass
    return result.returncode == 0


def _read_note_json(name: str) -> list[dict]:
    safe_folder = _escape_as(FOLDER)
    safe_name = _escape_as(name)
    script = f"""
tell application "Notes"
    if not (exists folder "{safe_folder}") then return ""
    set f to folder "{safe_folder}"
    set matching to (notes of f whose name begins with "{safe_name}")
    if length of matching is 0 then return ""
    return body of item 1 of matching
end tell
"""
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    raw = _strip_html(result.stdout.strip())
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except (json.JSONDecodeError, TypeError):
        return []


def _to_html(text: str) -> str:
    escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    lines = escaped.split('\n')
    return ''.join(
        f'<div>{line}</div>' if line.strip() else '<div><br></div>'
        for line in lines
    )


def write_report(deliveries: list) -> bool:
    """Write formatted delivery summary to DELIVERY REPORT note."""
    _ensure_notes_running()

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [f'{REPORT_NOTE} — Updated {now}', '']

    grouped: dict[str, list] = {}
    for d in deliveries:
        grouped.setdefault(d.status, []).append(d)

    if not deliveries:
        lines.append('No delivery messages found.')
    else:
        for status in _STATUS_ORDER:
            items = grouped.get(status, [])
            if not items:
                continue
            icon = _STATUS_ICONS[status]
            label = _STATUS_LABELS[status]
            lines.append(f'{icon} {label} ({len(items)})')
            lines.append('─' * 56)
            for d in items:
                carrier = d.carrier.ljust(10)
                tracking = d.tracking or '—'
                eta_part = f' — {d.eta}' if d.eta else ''
                source_tag = f'[{d.source}]'
                lines.append(f'  • {carrier} {tracking}{eta_part}  {source_tag}')
                if hasattr(d, 'subject') and d.subject:
                    lines.append(f'    {d.subject[:70]}')
                elif hasattr(d, 'text') and d.text:
                    snippet = d.text.replace('\n', ' ')[:70]
                    lines.append(f'    {snippet}')
            lines.append('')

    html = _to_html('\n'.join(lines))
    return _write_note(REPORT_NOTE, html)


def archive_transcripts(messages: list) -> bool:
    """Append new delivery messages to DELIVERY TRANSCRIPTS note as JSON."""
    if not messages:
        return True
    _ensure_notes_running()

    existing = _read_note_json(TRANSCRIPTS_NOTE)
    existing_ids = {r.get('rowid') or r.get('msg_id') for r in existing}

    now = datetime.now(timezone.utc).isoformat()
    new_records = []
    for m in messages:
        uid = getattr(m, 'rowid', None) or getattr(m, 'msg_id', None)
        if uid in existing_ids:
            continue
        record = {
            'archived_at': now,
            'date': m.date,
            'sender': m.sender,
            'carrier': m.carrier,
            'tracking': m.tracking,
            'status': m.status,
            'eta': m.eta,
            'source': m.source,
        }
        if hasattr(m, 'rowid'):
            record['rowid'] = m.rowid
            record['text'] = m.text
        else:
            record['msg_id'] = m.msg_id
            record['thread_id'] = m.thread_id
            record['subject'] = m.subject
            record['snippet'] = m.snippet
        new_records.append(record)

    if not new_records:
        return True

    all_records = existing + new_records
    json_text = json.dumps(all_records, indent=2, ensure_ascii=False)
    escaped = json_text.replace('<', '&lt;').replace('>', '&gt;')
    lines = escaped.split('\n')
    html = f'<div>{TRANSCRIPTS_NOTE}</div>' + ''.join(
        f'<div>{line}</div>' if line.strip() else '<div><br></div>'
        for line in lines
    )
    return _write_note(TRANSCRIPTS_NOTE, html)
