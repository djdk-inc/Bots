"""Fetch receipt URLs and save as PDFs using headless Chromium via Playwright.

On fetch failure, writes a fallback .txt file with whatever we could parse
from the message text (business, URL, order info) so there's always a record.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

from imessage_scanner import ReceiptMessage

ICLOUD_RECEIPTS = (
    Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Receipts/iMessage"
)

_LOAD_TIMEOUT_MS = 30_000
_CLICK_TIMEOUT_MS = 3_000


@dataclass
class FetchResult:
    ok: bool
    path: Optional[Path]  # PDF (ok=True) or .txt fallback (ok=False, partial=True)
    partial: bool = False  # text fallback written
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: Optional[str]) -> str:
    if not text:
        return "unknown"
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return slug.strip("_")[:40] or "unknown"


def _dest_path(msg: ReceiptMessage, output_dir: Path, ext: str = ".pdf") -> Path:
    month_dir = output_dir / msg.sent_at.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    date_str = msg.sent_at.strftime("%Y-%m-%d")
    business_slug = _slugify(msg.business)
    return month_dir / f"{date_str}_{business_slug}_{msg.rowid}{ext}"


def _dismiss_cookie_banners(page: Page):
    for sel in [
        'button:has-text("Allow all")',
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("I agree")',
    ]:
        try:
            page.click(sel, timeout=1_500)
            page.wait_for_timeout(800)
            return
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Site-specific handlers
# ---------------------------------------------------------------------------

def _expand_flo_io(page: Page):
    """flo.io collapses items and hides totals; two clicks to reveal."""
    try:
        page.click("text=Order from:", timeout=_CLICK_TIMEOUT_MS)
        page.wait_for_timeout(800)
    except Exception:
        pass
    try:
        page.click("text=View receipt", timeout=_CLICK_TIMEOUT_MS)
        page.wait_for_timeout(1_200)
    except Exception:
        pass


_SITE_HANDLERS = {
    "flo.io": _expand_flo_io,
}


def _apply_site_handler(page: Page, url: str):
    for hostname, handler in _SITE_HANDLERS.items():
        if hostname in url:
            handler(page)
            return


# ---------------------------------------------------------------------------
# Fallback text extraction
# ---------------------------------------------------------------------------

_ORDER_RE = re.compile(r"(?:order|order\s*#?|#)\s*([\w-]{4,})", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$[\d,]+\.\d{2}")


def _write_fallback(msg: ReceiptMessage, output_dir: Path, error: str) -> Path:
    """Write a plain-text stub with whatever we could parse from the message."""
    dest = _dest_path(msg, output_dir, ext=".txt")
    order = next(iter(_ORDER_RE.findall(msg.text)), None)
    amounts = _AMOUNT_RE.findall(msg.text)
    lines = [
        f"RECEIPT (fetch failed — {error})",
        f"Date:     {msg.sent_at.strftime('%Y-%m-%d %H:%M')} UTC",
        f"Sender:   {msg.sender}",
        f"Business: {msg.business or 'unknown'}",
        f"URL:      {msg.url}",
    ]
    if order:
        lines.append(f"Order #:  {order}")
    if amounts:
        lines.append(f"Amounts:  {', '.join(amounts)}")
    lines.append("")
    lines.append("Original message:")
    lines.append(msg.text)
    dest.write_text("\n".join(lines))
    return dest


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class ReceiptFetcher:
    def __init__(self, output_dir: Path = ICLOUD_RECEIPTS):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, msg: ReceiptMessage) -> FetchResult:
        """
        Fetch receipt URL and save as PDF.

        On success: returns FetchResult(ok=True, path=<pdf>).
        On failure: writes a .txt fallback and returns FetchResult(ok=False, partial=True, path=<txt>).
        """
        dest = _dest_path(msg, self.output_dir, ext=".pdf")
        # Remove stale file so hash check in state store isn't fooled by old content
        dest.unlink(missing_ok=True)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.goto(msg.url, wait_until="networkidle", timeout=_LOAD_TIMEOUT_MS)
                page.wait_for_timeout(1_500)
                _dismiss_cookie_banners(page)
                _apply_site_handler(page, msg.url)
                page.wait_for_timeout(500)
                page.pdf(
                    path=str(dest),
                    format="Letter",
                    print_background=True,
                    margin={"top": "0.5in", "bottom": "0.5in",
                            "left": "0.5in", "right": "0.5in"},
                )
                browser.close()
            return FetchResult(ok=True, path=dest)

        except PWTimeout as e:
            error = "timeout"
        except Exception as e:
            error = str(e)[:120]

        fallback = _write_fallback(msg, self.output_dir, error)
        return FetchResult(ok=False, partial=True, path=fallback, error=error)
