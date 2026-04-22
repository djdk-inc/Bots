#!/usr/bin/env python3
"""Block phone numbers via macOS com.apple.cmfsyncagent block list."""

import plistlib
import subprocess
import re
import tempfile
import os
from datetime import datetime
from pathlib import Path

PLIST = Path.home() / "Library/Preferences/com.apple.cmfsyncagent.plist"
TOP    = "__kCMFBlockListStoreTopLevelKey"
ARRAY  = "__kCMFBlockListStoreArrayKey"
REV    = "__kCMFBlockListStoreRevisionKey"
REV_TS = "__kCMFBlockListStoreRevisionTimestampKey"

# Longest-prefix-first for accuracy
COUNTRY_CODES = {
    "+1":   "us", "+44":  "gb", "+61":  "au", "+63":  "ph",
    "+56":  "cl", "+86":  "cn", "+52":  "mx", "+49":  "de",
    "+33":  "fr", "+81":  "jp", "+82":  "kr", "+91":  "in",
    "+55":  "br", "+27":  "za", "+7":   "ru", "+39":  "it",
    "+34":  "es", "+31":  "nl", "+46":  "se", "+47":  "no",
    "+45":  "dk", "+358": "fi", "+41":  "ch", "+43":  "at",
    "+32":  "be", "+351": "pt", "+48":  "pl", "+420": "cz",
    "+36":  "hu", "+40":  "ro", "+30":  "gr", "+90":  "tr",
    "+972": "il", "+966": "sa", "+971": "ae", "+65":  "sg",
    "+66":  "th", "+84":  "vn", "+60":  "my", "+62":  "id",
    "+64":  "nz", "+54":  "ar", "+57":  "co", "+51":  "pe",
}


def _is_phone(sender: str) -> bool:
    return bool(re.match(r"^\+?\d[\d\s\-().]{6,}$", sender))


def _country_code(number: str) -> str:
    for prefix in sorted(COUNTRY_CODES, key=len, reverse=True):
        if number.startswith(prefix):
            return COUNTRY_CODES[prefix]
    return "us"


def _normalize(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number)
    if not digits.startswith("+"):
        digits = "+1" + digits
    return digits


def block_numbers(senders: list[str]) -> int:
    phones = [_normalize(s) for s in senders if _is_phone(s)]
    if not phones:
        return 0

    with open(PLIST, "rb") as f:
        plist = plistlib.load(f)

    block_list = plist[TOP]
    existing = {e["__kCMFItemPhoneNumberUnformattedKey"] for e in block_list[ARRAY]}

    added = 0
    for number in phones:
        if number not in existing:
            block_list[ARRAY].append({
                "__kCMFItemPhoneNumberCountryCodeKey": _country_code(number),
                "__kCMFItemPhoneNumberUnformattedKey": number,
                "__kCMFItemTypeKey": 0,
                "__kCMFItemVersionKey": 1,
            })
            existing.add(number)
            added += 1

    if added:
        block_list[REV]    = block_list[REV] + 1
        block_list[REV_TS] = datetime.utcnow()
        # Write to a temp file beside the target, then atomic rename
        # so a failed write never corrupts the original.
        tmp_fd, tmp_path = tempfile.mkstemp(dir=PLIST.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
            os.replace(tmp_path, PLIST)
        except Exception:
            os.unlink(tmp_path)
            raise
        subprocess.run(["killall", "cmfsyncagent"], capture_output=True)

    return added
