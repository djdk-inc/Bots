#!/usr/bin/env python3
"""
iMessage Receipt Bot — finds SMS receipt links, saves PDFs to iCloud Drive,
archives metadata to Apple Notes (RECEIPTS), then deletes the source messages.

Order of operations per message:
  1. Fetch receipt URL → PDF (or .txt fallback on failure)
  2. Archive metadata to RECEIPTS note in Apple Notes
  3. Delete iMessage row from chat.db  ← only if step 2 succeeded

Runs daily at 9am PST via cron. Uses SHA256 hash deduplication so re-runs
are safe and already-fetched receipts are skipped.

Usage:
    python imessage_bot.py [--output DIR] [--dry-run] [--reset] [--refetch]

Requires Full Disk Access for python3:
    System Settings → Privacy & Security → Full Disk Access
"""
import argparse
import sys
from pathlib import Path

from imessage_scanner import IMessageScanner, MESSAGES_DB, ReceiptMessage
from imessage_fetcher import ReceiptFetcher, ICLOUD_RECEIPTS
from notes_writer import archive_receipts
from message_deleter import delete_messages
from state import StateStore


def _receipt_record(msg: ReceiptMessage, pdf_path: Path, status: str) -> dict:
    return {
        "rowid": msg.rowid,
        "date": msg.sent_at.isoformat(),
        "sender": msg.sender,
        "business": msg.business or msg.sender,
        "url": msg.url,
        "status": status,
        "file": pdf_path.name if pdf_path else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ICLOUD_RECEIPTS, metavar="DIR")
    parser.add_argument("--dry-run", action="store_true",
                        help="Find receipt messages but don't fetch, archive, or delete")
    parser.add_argument("--reset", action="store_true",
                        help="Clear all state and re-scan from the beginning")
    parser.add_argument("--refetch", action="store_true",
                        help="Re-fetch and overwrite previously saved receipts")
    args = parser.parse_args()

    store = StateStore()
    if args.reset:
        store.last_rowid = 0
        store._receipts = {}

    output_dir = args.output.expanduser()
    fetcher = None if args.dry_run else ReceiptFetcher(output_dir=output_dir)

    # Also retry any previously failed/partial rowids (scan from 0 to catch them)
    retry_rowids: set[int] = set()
    if not args.dry_run:
        retry_rowids = {
            int(rid)
            for rid, rec in store._receipts.items()
            if rec.get("status") in ("failed", "partial")
        }
        if retry_rowids:
            print(f"Retrying {len(retry_rowids)} previously failed receipt(s)...")

    stats = {"found": 0, "skipped": 0, "saved": 0, "partial": 0, "failed": 0, "deleted": 0}
    to_archive: list[dict] = []
    to_delete: list[int] = []

    scan_from = 0 if (retry_rowids or args.refetch) else store.last_rowid

    try:
        with IMessageScanner() as scanner:
            for msg in scanner.find_receipt_messages(since_rowid=scan_from):
                store.advance_rowid(msg.rowid)

                if not args.refetch and not args.dry_run and store.is_done(msg.rowid):
                    stats["skipped"] += 1
                    continue

                stats["found"] += 1
                business = msg.business or msg.sender
                print(f"  [{msg.sent_at.strftime('%Y-%m-%d')}] {business}")
                print(f"    {msg.url}")

                if args.dry_run:
                    continue

                result = fetcher.fetch(msg)

                if result.ok:
                    store.mark_ok(msg.rowid, msg.url, result.path)
                    stats["saved"] += 1
                    status = "ok"
                    print(f"    → {result.path}")
                elif result.partial:
                    store.mark_partial(msg.rowid, msg.url, result.path)
                    stats["partial"] += 1
                    status = "partial"
                    print(f"    ! fetch failed ({result.error}) — fallback: {result.path.name}")
                else:
                    store.mark_failed(msg.rowid, msg.url, result.error or "unknown")
                    stats["failed"] += 1
                    print(f"    ! failed: {result.error}")
                    continue  # don't archive or delete if we got nothing at all

                to_archive.append(_receipt_record(msg, result.path, status))
                to_delete.append(msg.rowid)

    except FileNotFoundError:
        print(f"Error: iMessage database not found at {MESSAGES_DB}", file=sys.stderr)
        print("Grant Full Disk Access to python3 in System Settings → Privacy & Security.", file=sys.stderr)
        sys.exit(1)

    # Archive to Notes first — only delete if archive succeeds
    if to_archive:
        print(f"\nArchiving {len(to_archive)} receipt(s) to Notes ({', '.join(r['business'] for r in to_archive)})...")
        archived = archive_receipts(to_archive)
        if archived:
            deleted = delete_messages(to_delete)
            stats["deleted"] = deleted
            print(f"    Deleted {deleted} message(s) from iMessage.")
        else:
            print("    Notes archive failed — skipping message deletion to be safe.", file=sys.stderr)

    if not args.dry_run:
        store.save()

    print(
        f"\nFound: {stats['found']}  "
        f"Saved: {stats['saved']}  "
        f"Partial: {stats['partial']}  "
        f"Failed: {stats['failed']}  "
        f"Skipped: {stats['skipped']}  "
        f"Deleted: {stats['deleted']}"
    )
    summary = store.summary()
    print(f"State: {summary['ok']} ok / {summary['failed']} failed / {summary['partial']} partial")
    if args.dry_run:
        print("(dry run — nothing saved, archived, or deleted)")


if __name__ == "__main__":
    main()
