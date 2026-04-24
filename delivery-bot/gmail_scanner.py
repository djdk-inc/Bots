"""Scan Gmail for shipping/delivery emails using the Gmail API."""
import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Carrier sender patterns ───────────────────────────────────────────────────

_CARRIER_SENDERS = {
    'UPS':       re.compile(r'@(?:e\.)?ups\.com', re.IGNORECASE),
    'FedEx':     re.compile(r'@(?:e\.)?fedex\.com', re.IGNORECASE),
    'USPS':      re.compile(r'@(?:email\.)?usps\.com', re.IGNORECASE),
    'Amazon':    re.compile(r'@(?:amazon\.com|amazonsesmailer\.com|marketplace\.amazon)', re.IGNORECASE),
    'DHL':       re.compile(r'@(?:dhl\.com|dhl\.de)', re.IGNORECASE),
    'OnTrac':    re.compile(r'@ontrac\.com', re.IGNORECASE),
    'LaserShip': re.compile(r'@lasership\.com', re.IGNORECASE),
    'Instacart': re.compile(r'@instacart\.com', re.IGNORECASE),
    'Shipt':     re.compile(r'@shipt\.com', re.IGNORECASE),
}

_SHIPPING_SUBJECT_RE = re.compile(
    r'\b(?:shipped|delivered|delivery|tracking|out\s+for\s+delivery|'
    r'package|shipment|order\s+(?:shipped|delivered|update)|'
    r'on\s+its\s+way|arriving|estimated\s+delivery)\b',
    re.IGNORECASE,
)

# ── Status detection (same logic as iMessage scanner) ────────────────────────

_STATUS_RE = {
    'out_for_delivery': re.compile(
        r'out\s+for\s+delivery|arriving\s+today|delivery\s+today|on\s+its\s+way.*today',
        re.IGNORECASE),
    'delivered': re.compile(
        r'has\s+been\s+delivered|was\s+delivered|package\s+delivered|'
        r'left\s+at\s+(?:front\s+)?door|successfully\s+delivered|delivery\s+complete',
        re.IGNORECASE),
    'attempted': re.compile(
        r'attempted\s+delivery|missed\s+delivery|unable\s+to\s+deliver|redelivery',
        re.IGNORECASE),
    'exception': re.compile(
        r'delivery\s+exception|weather\s+delay|address.*(?:issue|problem)|returned\s+to\s+sender',
        re.IGNORECASE),
    'in_transit': re.compile(
        r'in\s+transit|on\s+its\s+way|shipped|expected\s+delivery|estimated\s+delivery|arriving\s+by',
        re.IGNORECASE),
}

_TRACKING_RE = re.compile(
    r'\b(1Z[A-Z0-9]{16}|TBA\d{12,13}|9[245]\d{18}|\d{12}|\d{15}|\d{20}|\d{10,11})\b'
)

_ETA_RE = re.compile(
    r'(?:by|before|arriving|estimated|expected)\s+'
    r'((?:today|tomorrow|\w+ \d{1,2}(?:,? \d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?))',
    re.IGNORECASE,
)


@dataclass
class GmailDelivery:
    msg_id: str
    thread_id: str
    date: str
    sender: str
    subject: str
    carrier: str
    tracking: Optional[str]
    status: str
    eta: Optional[str]
    snippet: str
    source: str = 'gmail'


def _decode_header(encoded: str) -> str:
    import email.header
    parts = email.header.decode_header(encoded)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            decoded.append(part)
    return ''.join(decoded)


def _classify_status(text: str) -> str:
    for status in ('out_for_delivery', 'delivered', 'attempted', 'exception', 'in_transit'):
        if _STATUS_RE[status].search(text):
            return status
    return 'unknown'


def _detect_carrier(sender: str, subject: str, body: str) -> str:
    combined = f'{sender} {subject} {body[:500]}'
    for carrier, pattern in _CARRIER_SENDERS.items():
        if pattern.search(sender):
            return carrier
    # Fallback: check subject/body for carrier names
    for carrier in ('UPS', 'FedEx', 'USPS', 'Amazon', 'DHL', 'OnTrac', 'LaserShip'):
        if re.search(r'\b' + re.escape(carrier) + r'\b', combined, re.IGNORECASE):
            return carrier
    return 'Unknown'


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from Gmail message payload."""
    mime_type = payload.get('mimeType', '')
    body_data = payload.get('body', {}).get('data', '')

    if mime_type == 'text/plain' and body_data:
        return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='replace')

    for part in payload.get('parts', []):
        text = _extract_body(part)
        if text:
            return text

    if body_data:
        return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='replace')

    return ''


def build_service():
    """Build Gmail API service using stored OAuth token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    import config

    token_path = Path(config.GMAIL_TOKEN).expanduser()
    creds = Credentials.from_authorized_user_file(str(token_path), config.GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def fetch_delivery_emails(service, processed_ids: set[str]) -> list[GmailDelivery]:
    """Search Gmail for delivery emails not yet processed."""
    query = (
        '('
        'from:ups.com OR from:fedex.com OR from:usps.com OR from:amazon.com OR '
        'from:dhl.com OR from:ontrac.com OR from:lasership.com OR '
        'from:instacart.com OR from:shipt.com'
        ') OR ('
        'subject:(shipped OR delivered OR "out for delivery" OR "delivery notification" OR '
        '"package delivered" OR "on its way" OR "arriving today")'
        ')'
        ' newer_than:30d'
    )

    results = service.users().messages().list(
        userId='me', q=query, maxResults=100
    ).execute()

    messages = results.get('messages', [])
    deliveries = []

    for msg_ref in messages:
        msg_id = msg_ref['id']
        if msg_id in processed_ids:
            continue

        msg = service.users().messages().get(
            userId='me', id=msg_id, format='full'
        ).execute()

        headers = {h['name'].lower(): h['value'] for h in msg['payload'].get('headers', [])}
        subject = _decode_header(headers.get('subject', ''))
        sender = headers.get('from', '')
        date = headers.get('date', '')
        snippet = msg.get('snippet', '')

        if not _SHIPPING_SUBJECT_RE.search(subject + ' ' + snippet):
            continue

        body = _extract_body(msg['payload'])
        combined = f'{subject} {snippet} {body[:1000]}'

        carrier = _detect_carrier(sender, subject, body)
        status = _classify_status(combined)
        tracking_m = _TRACKING_RE.search(combined)
        tracking = tracking_m.group(1) if tracking_m else None
        eta_m = _ETA_RE.search(combined)
        eta = eta_m.group(1) if eta_m else None

        deliveries.append(GmailDelivery(
            msg_id=msg_id,
            thread_id=msg['threadId'],
            date=date,
            sender=sender,
            subject=subject,
            carrier=carrier,
            tracking=tracking,
            status=status,
            eta=eta,
            snippet=snippet[:200],
        ))

    return deliveries


def archive_thread(service, thread_id: str) -> bool:
    """Remove from inbox and add Deliveries/Processed label."""
    try:
        # Ensure the label exists
        label_id = _get_or_create_label(service, 'Deliveries/Processed')
        service.users().threads().modify(
            userId='me',
            id=thread_id,
            body={
                'addLabelIds': [label_id] if label_id else [],
                'removeLabelIds': ['INBOX'],
            }
        ).execute()
        return True
    except Exception as e:
        print(f'  Gmail archive failed for thread {thread_id}: {e}')
        return False


def _get_or_create_label(service, name: str) -> Optional[str]:
    try:
        labels = service.users().labels().list(userId='me').execute().get('labels', [])
        for label in labels:
            if label['name'].lower() == name.lower():
                return label['id']
        result = service.users().labels().create(
            userId='me',
            body={'name': name, 'messageListVisibility': 'show', 'labelListVisibility': 'labelShow'}
        ).execute()
        return result['id']
    except Exception:
        return None
