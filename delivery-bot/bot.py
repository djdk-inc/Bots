#!/usr/bin/env python3
"""
Delivery Bot — scans iMessage + Gmail for shipping notifications, writes a
DELIVERY REPORT and DELIVERY TRANSCRIPTS note in Apple Notes, then cleans up
delivered messages.

Runs daily via cron. Use --dry-run to preview without writing or deleting.

Usage:
    python bot.py [--dry-run] [--no-gmail] [--days N]
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config
from delivery_scanner import fetch_delivery_messages, delete_from_db
from notes_writer import write_report, archive_transcripts
from state import StateStore


def _load_gmail_service():
    try:
        from gmail_scanner import build_service
        return build_service()
    except FileNotFoundError:
        print('  Gmail token not found — run setup_gmail.py first. Skipping Gmail.', file=sys.stderr)
        return None
    except Exception as e:
        print(f'  Gmail unavailable: {e}. Skipping.', file=sys.stderr)
        return None


def _fetch_gmail_deliveries(service, processed_ids: set[str]) -> list:
    from gmail_scanner import fetch_delivery_emails
    try:
        return fetch_delivery_emails(service, processed_ids)
    except Exception as e:
        print(f'  Gmail fetch error: {e}', file=sys.stderr)
        return []


def _is_old_delivered(date_str: str) -> bool:
    """Return True if a 'delivered' message is older than the cleanup threshold."""
    try:
        msg_dt = datetime.strptime(date_str[:16], '%Y-%m-%d %H:%M')
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            msg_dt = parsedate_to_datetime(date_str).replace(tzinfo=None)
        except Exception:
            return False
    cutoff = datetime.now() - timedelta(days=config.CLEANUP_DELIVERED_DAYS)
    return msg_dt < cutoff


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true',
                        help='Find messages but skip writing to Notes and deleting')
    parser.add_argument('--no-gmail', action='store_true',
                        help='Skip Gmail scanning')
    parser.add_argument('--days', type=int, default=config.SCAN_DAYS,
                        help=f'Days of iMessage history to scan (default: {config.SCAN_DAYS})')
    parser.add_argument('--all', action='store_true',
                        help='Scan entire iMessage history and delete all delivered messages regardless of age')
    args = parser.parse_args()
    if args.all:
        args.days = 36500

    store = StateStore()
    all_deliveries: list = []

    # ── iMessage scan ─────────────────────────────────────────────────────────
    print(f'Scanning last {args.days} day(s) of iMessages for deliveries…')
    try:
        imessage_deliveries = fetch_delivery_messages(days=args.days)
    except Exception as e:
        print(f'  iMessage scan error: {e}', file=sys.stderr)
        imessage_deliveries = []

    new_imessage = [
        d for d in imessage_deliveries
        if d.rowid not in store.processed_imessage_rowids()
    ]
    print(f'  Found {len(imessage_deliveries)} delivery message(s) '
          f'({len(new_imessage)} new)')
    all_deliveries.extend(imessage_deliveries)

    # ── Gmail scan ────────────────────────────────────────────────────────────
    gmail_deliveries = []
    service = None
    if not args.no_gmail:
        print('Scanning Gmail for delivery emails…')
        service = _load_gmail_service()
        if service:
            gmail_deliveries = _fetch_gmail_deliveries(service, store.processed_gmail_ids())
            print(f'  Found {len(gmail_deliveries)} delivery email(s)')
            all_deliveries.extend(gmail_deliveries)

    # ── Report ────────────────────────────────────────────────────────────────
    _print_report(all_deliveries)

    if args.dry_run:
        print('\n(dry run — nothing written or deleted)')
        return

    # ── Archive transcripts ───────────────────────────────────────────────────
    new_for_archive = new_imessage + gmail_deliveries
    if new_for_archive:
        print(f'\nArchiving {len(new_for_archive)} new message(s) to Notes…')
        if archive_transcripts(new_for_archive):
            for d in new_imessage:
                store.mark_imessage_processed(d.rowid)
            for d in gmail_deliveries:
                store.mark_gmail_processed(d.msg_id)
        else:
            print('  Archive failed — skipping cleanup to be safe.', file=sys.stderr)
            store.save()
            return

    # ── Write delivery report ─────────────────────────────────────────────────
    print('Updating DELIVERY REPORT note…')
    write_report(all_deliveries)

    # ── Cleanup: delete delivered iMessages ───────────────────────────────────
    to_delete = [
        d.rowid for d in imessage_deliveries
        if d.status == 'delivered' and (args.all or _is_old_delivered(d.date))
    ]
    if to_delete:
        label = 'all' if args.all else f'older than {config.CLEANUP_DELIVERED_DAYS}d'
        print(f'Deleting {len(to_delete)} delivered iMessage(s) ({label})…')
        deleted = delete_from_db(to_delete)
        print(f'  Deleted {deleted} message(s).')

    # ── Cleanup: archive delivered Gmail threads ───────────────────────────────
    if service and gmail_deliveries:
        from gmail_scanner import archive_thread
        to_archive_gmail = [
            d for d in gmail_deliveries
            if d.status == 'delivered' and _is_old_delivered(d.date)
        ]
        if to_archive_gmail:
            print(f'Archiving {len(to_archive_gmail)} delivered Gmail thread(s)…')
            for d in to_archive_gmail:
                archive_thread(service, d.thread_id)

    store.save()
    print('\nDone.')


def _print_report(deliveries: list) -> None:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n{"━"*60}')
    print(f'  Delivery Bot  ·  {now}')
    print(f'{"━"*60}')
    if not deliveries:
        print('  No delivery messages found.')
        return

    icons = {
        'out_for_delivery': '📦', 'delivered': '✅', 'in_transit': '🚚',
        'attempted': '⚠️ ', 'exception': '🚨', 'unknown': '❓',
    }
    order = ['out_for_delivery', 'delivered', 'attempted', 'exception', 'in_transit', 'unknown']

    grouped: dict[str, list] = {}
    for d in deliveries:
        grouped.setdefault(d.status, []).append(d)

    for status in order:
        items = grouped.get(status, [])
        if not items:
            continue
        label = status.replace('_', ' ').upper()
        print(f'\n  {icons.get(status, "•")} {label} ({len(items)})')
        print(f'  {"─"*54}')
        for d in items[:8]:
            carrier = d.carrier.ljust(10)
            tracking = (d.tracking or '—')[:20]
            eta = f' — {d.eta}' if d.eta else ''
            src = f'[{d.source}]'
            print(f'  {carrier} {tracking}{eta}  {src}')
        if len(items) > 8:
            print(f'  … and {len(items) - 8} more')
    print()


if __name__ == '__main__':
    main()
