"""Scan iMessage chat.db for delivery/shipping notifications."""
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

MESSAGES_DB = Path.home() / "Library/Messages/chat.db"
_MAC_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# ── Carrier detection ─────────────────────────────────────────────────────────

_CARRIER_RE = re.compile(
    r'\b(UPS|FedEx|Fed\s*Ex|USPS|DHL|OnTrac|LaserShip|LSO|Amazon|Instacart|Shipt|GoPuff)\b',
    re.IGNORECASE,
)

_CARRIER_NORM = {
    'ups': 'UPS', 'fedex': 'FedEx', 'fed ex': 'FedEx',
    'usps': 'USPS', 'dhl': 'DHL', 'ontrac': 'OnTrac',
    'lasership': 'LaserShip', 'lso': 'LaserShip',
    'amazon': 'Amazon', 'instacart': 'Instacart',
    'shipt': 'Shipt', 'gopuff': 'GoPuff',
}

# ── Tracking number extraction ────────────────────────────────────────────────

_TRACKING_PATTERNS = [
    re.compile(r'\b(1Z[A-Z0-9]{16})\b'),                    # UPS
    re.compile(r'\b(TBA\d{12,13})\b'),                       # Amazon Logistics
    re.compile(r'\b(9[24]\d{18})\b'),                        # USPS Priority/First Class
    re.compile(r'\b(9[45]\d{18})\b'),                        # USPS Certified
    re.compile(r'\b(\d{12}|\d{15}|\d{20})\b'),              # FedEx
    re.compile(r'\b(\d{10,11})\b'),                          # DHL Express
]

# ── Status classification ─────────────────────────────────────────────────────

_STATUS_PATTERNS = {
    'out_for_delivery': [
        r'out\s+for\s+delivery',
        r'arriving\s+today',
        r'delivery\s+today',
        r'will\s+be\s+delivered\s+today',
        r'on\s+its\s+way.*today',
    ],
    'delivered': [
        r'has\s+been\s+delivered',
        r'was\s+delivered',
        r'package\s+delivered',
        r'left\s+at\s+(?:your\s+)?(?:front\s+)?door',
        r'left\s+at\s+(?:front\s+)?porch',
        r'left\s+in\s+mailbox',
        r'delivered\s+to',
        r'your\s+delivery\s+is\s+complete',
        r'successfully\s+delivered',
    ],
    'attempted': [
        r'attempted\s+delivery',
        r'delivery\s+attempt',
        r'missed\s+delivery',
        r'unable\s+to\s+deliver',
        r'redelivery',
        r'pick\s+up\s+at\s+(?:a\s+)?(?:local\s+)?(?:facility|location|store)',
    ],
    'exception': [
        r'delivery\s+exception',
        r'delay(?:ed)?',
        r'weather\s+delay',
        r'address\s+(?:issue|problem|undeliverable)',
        r'returned\s+to\s+sender',
    ],
    'in_transit': [
        r'in\s+transit',
        r'on\s+its\s+way',
        r'shipped',
        r'expected\s+delivery',
        r'estimated\s+delivery',
        r'arriving\s+(?:by|on)',
        r'package\s+is\s+on\s+the\s+way',
    ],
}

_COMPILED_STATUS = {
    status: [re.compile(p, re.IGNORECASE) for p in patterns]
    for status, patterns in _STATUS_PATTERNS.items()
}

# Keywords that indicate this is a delivery message at all
_DELIVERY_KEYWORDS_RE = re.compile(
    r'\b(?:package|delivery|delivered|shipment|shipped|tracking|in\s+transit|'
    r'out\s+for\s+delivery|estimated\s+delivery|expected\s+delivery)\b',
    re.IGNORECASE,
)

# ── ETA extraction ────────────────────────────────────────────────────────────

_ETA_RE = re.compile(
    r'(?:by|before|until|arriving)\s+((?:today|tomorrow|\d{1,2}(?::\d{2})?\s*(?:am|pm)|'
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s*\d{4})?))',
    re.IGNORECASE,
)

_BLOB_TEXT_RE = re.compile(rb'[\x20-\x7e]{4,}')


@dataclass
class DeliveryMessage:
    rowid: int
    date: str
    sender: str
    text: str
    carrier: str
    tracking: Optional[str]
    status: str
    eta: Optional[str]
    source: str = 'imessage'


def _mac_ts_to_str(ts: int) -> str:
    if ts > 1_000_000_000_000:
        dt = _MAC_EPOCH + timedelta(seconds=ts / 1e9)
    else:
        dt = _MAC_EPOCH + timedelta(seconds=ts)
    return dt.astimezone().strftime('%Y-%m-%d %H:%M')


def _mac_ts_to_datetime(ts: int) -> datetime:
    if ts > 1_000_000_000_000:
        return _MAC_EPOCH + timedelta(seconds=ts / 1e9)
    return _MAC_EPOCH + timedelta(seconds=ts)


def _blob_to_text(blob: bytes) -> str:
    runs = _BLOB_TEXT_RE.findall(blob)
    return ' '.join(r.decode('ascii', errors='replace') for r in runs)


def _classify_status(text: str) -> str:
    for status in ('out_for_delivery', 'delivered', 'attempted', 'exception', 'in_transit'):
        for pattern in _COMPILED_STATUS[status]:
            if pattern.search(text):
                return status
    return 'unknown'


def _extract_carrier(text: str, sender: str) -> Optional[str]:
    combined = f'{sender} {text}'
    m = _CARRIER_RE.search(combined)
    if m:
        return _CARRIER_NORM.get(m.group(1).lower(), m.group(1))
    return None


def _extract_tracking(text: str) -> Optional[str]:
    for pattern in _TRACKING_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def _extract_eta(text: str) -> Optional[str]:
    m = _ETA_RE.search(text)
    return m.group(1).strip() if m else None


def _is_delivery_message(text: str, sender: str) -> bool:
    if _CARRIER_RE.search(sender):
        return True
    if not _DELIVERY_KEYWORDS_RE.search(text):
        return False
    return _CARRIER_RE.search(text) is not None


def fetch_delivery_messages(days: int) -> list[DeliveryMessage]:
    if not MESSAGES_DB.exists():
        return []

    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    shutil.copy2(MESSAGES_DB, tmp.name)

    apple_epoch_offset = 978307200
    cutoff_apple = (datetime.now() - timedelta(days=days)).timestamp() - apple_epoch_offset

    try:
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT m.ROWID AS rowid, m.date, m.text, m.attributedBody, h.id AS sender
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.rowid
            WHERE m.date / 1000000000 > ?
              AND m.is_from_me = 0
            ORDER BY m.date DESC
        """, (cutoff_apple,)).fetchall()
        conn.close()
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    results = []
    for row in rows:
        text = row['text'] or ''
        if not text and row['attributedBody']:
            text = _blob_to_text(bytes(row['attributedBody']))
        if not text:
            continue

        sender = row['sender'] or ''
        if not _is_delivery_message(text, sender):
            continue

        carrier = _extract_carrier(text, sender) or 'Unknown'
        status = _classify_status(text)
        tracking = _extract_tracking(text)
        eta = _extract_eta(text)
        date_str = _mac_ts_to_str(row['date'])

        results.append(DeliveryMessage(
            rowid=row['rowid'],
            date=date_str,
            sender=sender,
            text=text,
            carrier=carrier,
            tracking=tracking,
            status=status,
            eta=eta,
        ))

    return results


def delete_from_db(rowids: list[int]) -> int:
    if not rowids:
        return 0

    import shutil as _shutil
    from datetime import datetime as _dt

    backup_dir = Path.home() / '.imessage_cleaner_backups'
    backup_dir.mkdir(exist_ok=True)
    stamp = _dt.now().strftime('%Y%m%d_%H%M%S')
    _shutil.copy2(MESSAGES_DB, backup_dir / f'chat_{stamp}.db')

    conn = sqlite3.connect(MESSAGES_DB)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'after_delete_on_message_plugin'"
    ).fetchone()
    trigger_sql = row[0] if row else None

    if trigger_sql:
        conn.execute('DROP TRIGGER IF EXISTS after_delete_on_message_plugin')

    ph = ','.join('?' * len(rowids))
    cur = conn.execute(f'DELETE FROM message WHERE ROWID IN ({ph})', rowids)
    deleted = cur.rowcount

    if trigger_sql:
        conn.execute(trigger_sql)
    conn.commit()
    conn.close()
    return deleted
