"""
Scan ledger — persists scan state across sessions to avoid redundant Fireflies fetches.

Schema: test_output/scan_ledger.json
{
    "last_scan_at": "<ISO 8601 UTC string or null>",
    "imported_ids": ["<call_id>", ...],
    "rejected_ids": ["<call_id>", ...]
}
"""

import json
import os
from datetime import datetime, timezone
from typing import List, Optional

LEDGER_FILENAME = "scan_ledger.json"


def _ledger_path(output_dir: str) -> str:
    return os.path.join(output_dir, LEDGER_FILENAME)


def _empty_ledger() -> dict:
    return {
        "last_scan_at": None,
        "imported_ids": [],
        "rejected_ids": [],
    }


def load_ledger(output_dir: str) -> dict:
    """Load the ledger from disk. Returns an empty ledger if not found or corrupt."""
    path = _ledger_path(output_dir)
    if not os.path.exists(path):
        return _empty_ledger()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        # Ensure all keys exist (forward-compat for older ledger files)
        ledger = _empty_ledger()
        ledger.update(data)
        return ledger
    except (json.JSONDecodeError, OSError):
        return _empty_ledger()


def save_ledger(output_dir: str, ledger: dict) -> None:
    """Write the ledger to disk atomically."""
    os.makedirs(output_dir, exist_ok=True)
    path = _ledger_path(output_dir)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(ledger, f, indent=2)
    os.replace(tmp_path, path)


def get_known_ids(output_dir: str) -> set:
    """Return the union of imported and rejected IDs — all IDs we never want to re-show."""
    ledger = load_ledger(output_dir)
    return set(ledger["imported_ids"]) | set(ledger["rejected_ids"])


def get_last_scan_dt(output_dir: str) -> Optional[datetime]:
    """Return the last scan datetime (UTC-aware), or None if never scanned."""
    ledger = load_ledger(output_dir)
    raw = ledger.get("last_scan_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def record_scan(output_dir: str) -> None:
    """Update last_scan_at to now."""
    ledger = load_ledger(output_dir)
    ledger["last_scan_at"] = datetime.now(timezone.utc).isoformat()
    save_ledger(output_dir, ledger)


def record_imported(output_dir: str, call_ids: List[str]) -> None:
    """Add call IDs to imported_ids (deduped)."""
    if not call_ids:
        return
    ledger = load_ledger(output_dir)
    existing = set(ledger["imported_ids"])
    for cid in call_ids:
        if cid not in existing:
            ledger["imported_ids"].append(cid)
            existing.add(cid)
    save_ledger(output_dir, ledger)


def record_rejected(output_dir: str, call_ids: List[str]) -> None:
    """Add call IDs to rejected_ids (deduped)."""
    if not call_ids:
        return
    ledger = load_ledger(output_dir)
    existing = set(ledger["rejected_ids"])
    for cid in call_ids:
        if cid not in existing:
            ledger["rejected_ids"].append(cid)
            existing.add(cid)
    save_ledger(output_dir, ledger)
