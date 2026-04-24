#!/usr/bin/env python3
"""
One-time Gmail OAuth2 setup for the delivery bot.

Steps:
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable the Gmail API
  3. Create OAuth credentials (Desktop app) → Download credentials.json
  4. Place credentials.json at ~/.delivery-bot/credentials.json
  5. Run: python setup_gmail.py

The token.json it creates will be auto-refreshed by the bot going forward.
"""
from pathlib import Path
import sys

import config

CREDENTIALS = Path(config.GMAIL_CREDENTIALS).expanduser()
TOKEN = Path(config.GMAIL_TOKEN).expanduser()


def main():
    if not CREDENTIALS.exists():
        print(f'Error: credentials.json not found at {CREDENTIALS}', file=sys.stderr)
        print(__doc__)
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print('Missing dependency. Run: pip install google-auth-oauthlib', file=sys.stderr)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), config.GMAIL_SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN, 'w') as f:
        f.write(creds.to_json())

    print(f'Token saved to {TOKEN}')
    print('Gmail is ready. Run: python bot.py --dry-run to test.')


if __name__ == '__main__':
    main()
