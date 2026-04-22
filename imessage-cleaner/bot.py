#!/usr/bin/env python3
"""Daily iMessage cleanup — archive, block, delete. No approval needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from imessage_cleaner import fetch_messages, delete_from_db, CATEGORIES
from archive import archive_to_notes
from blocker import block_numbers
import config

DELETABLE    = [c for c in CATEGORIES if c != "legitimate"]
BLOCK_ON     = {"phishing", "job_scam", "ad"}


def main() -> None:
    messages = fetch_messages(days=config.SCAN_DAYS)
    if not messages:
        return

    grouped: dict = {}
    for m in messages:
        grouped.setdefault(m["category"], []).append(m)

    flagged = [m for cat in DELETABLE for m in grouped.get(cat, [])]
    if not flagged:
        return

    # 1. Archive to Notes first — skip everything if this fails
    if not archive_to_notes(flagged):
        print("Archive failed — aborting.", file=sys.stderr)
        return

    # 2. Block senders (phone numbers only, spam/phishing/scam categories)
    to_block = [m["sender"] for m in flagged if m["category"] in BLOCK_ON]
    blocked = block_numbers(to_block)

    # 3. Delete
    rowids = [m["rowid"] for m in flagged]
    deleted = delete_from_db(rowids)

    print(f"Archived {len(flagged)}, blocked {blocked}, deleted {deleted}.")


if __name__ == "__main__":
    main()
