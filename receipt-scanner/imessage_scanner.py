"""Scan iMessage database for SMS messages containing receipt URLs."""
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator, Optional

# attributedBody is a binary TypedStream (NSAttributedString archive).
# We can't easily fully parse it without ObjC, but the raw UTF-8 text and
# URLs are embedded directly in the blob and survive a UTF-8 error-replace decode.
_BLOB_TEXT_RE = re.compile(rb"[\x20-\x7e]{4,}")  # printable ASCII runs ≥4 chars

MESSAGES_DB = Path.home() / "Library/Messages/chat.db"

# 2001-01-01 UTC — Mac absolute time epoch
_MAC_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# Known receipt URL patterns (checked first, high confidence)
_KNOWN_URL_RE = re.compile(
    r"https?://\S*(?:"
    r"clover\.com/p"
    r"|squareup\.com/r(?:eceipt)?/"     # /receipt/... and /r/<hash>
    r"|receipt\.squareup\.com"
    r"|toasttab\.com.*?receipt"
    r"|toasttab\.com/card/"             # Toast card payment receipts
    r"|pay\.stripe\.com/receipt"
    r"|paypal\.com/receipt"
    r"|venmo\.com/receipt"
    r"|flo\.io/receipt"                 # Flo receipt platform
    r")\S*",
    re.IGNORECASE,
)

# Generic fallback: any URL that ends in /receipt or /receipt/...
_GENERIC_URL_RE = re.compile(
    r"https?://\S+/receipt[/\w?=&%-]*",
    re.IGNORECASE,
)

# Extract business name from "View your receipt from NO PULP: URL"
_BUSINESS_RE = re.compile(
    r"(?i)(?:view your |your )?receipt from ([^:\n]{1,60})(?::|$)",
)


@dataclass
class ReceiptMessage:
    rowid: int
    guid: str
    sender: str
    sent_at: datetime
    text: str
    business: Optional[str]
    url: str


def _mac_ts(ts: int) -> datetime:
    if ts > 1_000_000_000_000:  # stored in nanoseconds (macOS Catalina+)
        return _MAC_EPOCH + timedelta(seconds=ts / 1e9)
    return _MAC_EPOCH + timedelta(seconds=ts)


def _blob_to_text(blob: bytes) -> str:
    """Extract readable text from an attributedBody binary blob."""
    runs = _BLOB_TEXT_RE.findall(blob)
    return " ".join(r.decode("ascii", errors="replace") for r in runs)


def _extract_url(text: str) -> Optional[str]:
    m = _KNOWN_URL_RE.search(text)
    if m:
        return m.group(0).rstrip(".,)")
    m = _GENERIC_URL_RE.search(text)
    if m:
        return m.group(0).rstrip(".,)")
    return None


def _extract_business(text: str) -> Optional[str]:
    m = _BUSINESS_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


class IMessageScanner:
    """
    Reads the iMessage SQLite database (chat.db) and yields ReceiptMessage
    for each inbound message that contains a recognizable receipt URL.

    Copies chat.db to a temp file before opening so we don't hold a lock
    on the live database.
    """

    def __init__(self, db_path: Path = MESSAGES_DB):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(db_path, tmp.name)
        self._tmp_path = Path(tmp.name)
        self._conn = sqlite3.connect(self._tmp_path)
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()
        self._tmp_path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def find_receipt_messages(self, since_rowid: int = 0) -> Iterator[ReceiptMessage]:
        """Yield ReceiptMessage for inbound messages containing a receipt URL.

        Searches both the text column and the attributedBody blob. On newer macOS
        the message body lives only in the blob (a binary TypedStream archive).
        SQLite LIKE doesn't match inside BLOBs, so we use instr() for blob filtering
        and do a final URL check in Python.
        """
        cur = self._conn.execute(
            """
            SELECT m.rowid, m.guid, m.date, m.text, m.attributedBody, h.id AS sender
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.rowid
            WHERE m.rowid > ?
              AND m.is_from_me = 0
              AND (
                (m.text IS NOT NULL AND (
                    m.text LIKE '%receipt%'
                 OR m.text LIKE '%clover.com/p/%'
                 OR m.text LIKE '%squareup.com%'
                 OR m.text LIKE '%toasttab.com%'
                 OR m.text LIKE '%pay.stripe.com%'
                ))
                OR
                (m.attributedBody IS NOT NULL AND (
                    instr(m.attributedBody, 'receipt') > 0
                 OR instr(m.attributedBody, 'clover.com/p/') > 0
                 OR instr(m.attributedBody, 'squareup.com') > 0
                 OR instr(m.attributedBody, 'toasttab.com') > 0
                 OR instr(m.attributedBody, 'pay.stripe.com') > 0
                 OR instr(m.attributedBody, 'flo.io/receipt') > 0
                ))
              )
            ORDER BY m.rowid ASC
            """,
            (since_rowid,),
        )
        for row in cur:
            # Prefer plain text; fall back to extracting readable strings from blob
            text: str = row["text"] or ""
            if not text and row["attributedBody"]:
                text = _blob_to_text(bytes(row["attributedBody"]))

            url = _extract_url(text)
            if not url:
                continue
            yield ReceiptMessage(
                rowid=row["rowid"],
                guid=row["guid"],
                sender=row["sender"] or "unknown",
                sent_at=_mac_ts(row["date"]),
                text=text,
                business=_extract_business(text),
                url=url,
            )
