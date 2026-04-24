"""Track processed message IDs to avoid duplicate processing."""
import json
from pathlib import Path

import config

_STATE_PATH = Path(config.STATE_FILE).expanduser()


class StateStore:
    def __init__(self):
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        if _STATE_PATH.exists():
            with open(_STATE_PATH) as f:
                self._data = json.load(f)

    @property
    def last_imessage_rowid(self) -> int:
        return self._data.get('last_imessage_rowid', 0)

    @last_imessage_rowid.setter
    def last_imessage_rowid(self, v: int):
        self._data['last_imessage_rowid'] = v

    def processed_gmail_ids(self) -> set[str]:
        return set(self._data.get('processed_gmail_ids', []))

    def mark_gmail_processed(self, msg_id: str):
        ids = self.processed_gmail_ids()
        ids.add(msg_id)
        self._data['processed_gmail_ids'] = list(ids)

    def processed_imessage_rowids(self) -> set[int]:
        return set(self._data.get('processed_imessage_rowids', []))

    def mark_imessage_processed(self, rowid: int):
        ids = self.processed_imessage_rowids()
        ids.add(rowid)
        self._data['processed_imessage_rowids'] = list(ids)

    def save(self):
        with open(_STATE_PATH, 'w') as f:
            json.dump(self._data, f, indent=2)
