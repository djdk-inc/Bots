"""
Persistent state store for the receipt bot.

Each receipt message gets a record keyed by its iMessage rowid:
  - "ok"     : PDF fetched successfully; SHA256 hash stored for dedup
  - "failed" : fetch failed; will be retried on next run
  - "partial": URL timed out but we extracted what we could from message text
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATE_FILE = Path.home() / ".receipt_bot_imessage_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class StateStore:
    def __init__(self, path: Path = STATE_FILE):
        self._path = path
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.last_rowid: int = raw.get("last_rowid", 0)
        self._receipts: dict[str, dict] = raw.get("receipts", {})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_done(self, rowid: int) -> bool:
        """True if rowid was fetched successfully AND the PDF still exists with matching hash."""
        rec = self._receipts.get(str(rowid))
        if not rec or rec.get("status") != "ok":
            return False
        saved_hash = rec.get("hash")
        saved_path = rec.get("path")
        if not saved_hash or not saved_path:
            return False
        p = Path(saved_path)
        return p.exists() and _sha256(p) == saved_hash

    def needs_retry(self, rowid: int) -> bool:
        """True if rowid has never been processed or previously failed."""
        rec = self._receipts.get(str(rowid))
        return rec is None or rec.get("status") in ("failed", "partial")

    def attempt_count(self, rowid: int) -> int:
        return self._receipts.get(str(rowid), {}).get("attempts", 0)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def mark_ok(self, rowid: int, url: str, path: Path):
        file_hash = _sha256(path)
        existing = self._receipts.get(str(rowid), {})
        self._receipts[str(rowid)] = {
            "status": "ok",
            "url": url,
            "hash": file_hash,
            "path": str(path),
            "fetched_at": _now(),
            "attempts": existing.get("attempts", 0) + 1,
        }

    def mark_failed(self, rowid: int, url: str, error: str):
        existing = self._receipts.get(str(rowid), {})
        self._receipts[str(rowid)] = {
            "status": "failed",
            "url": url,
            "hash": existing.get("hash"),
            "path": existing.get("path"),
            "fetched_at": existing.get("fetched_at"),
            "attempts": existing.get("attempts", 0) + 1,
            "last_error": str(error)[:200],
            "last_attempt": _now(),
        }

    def mark_partial(self, rowid: int, url: str, path: Path):
        """URL fetch failed but we wrote a fallback text file."""
        existing = self._receipts.get(str(rowid), {})
        self._receipts[str(rowid)] = {
            "status": "partial",
            "url": url,
            "path": str(path),
            "fetched_at": _now(),
            "attempts": existing.get("attempts", 0) + 1,
        }

    def advance_rowid(self, rowid: int):
        self.last_rowid = max(self.last_rowid, rowid)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        self._path.write_text(json.dumps(
            {"last_rowid": self.last_rowid, "receipts": self._receipts},
            indent=2,
        ))

    def summary(self) -> dict:
        statuses = [r.get("status") for r in self._receipts.values()]
        return {
            "total": len(statuses),
            "ok": statuses.count("ok"),
            "failed": statuses.count("failed"),
            "partial": statuses.count("partial"),
        }
