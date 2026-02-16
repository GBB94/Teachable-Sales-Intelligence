"""
Global sent ledger for cross-sequence dedup.

JSONL file at data/mixmax_sent_ledger.jsonl. Append-only, keyed by email.
Prevents double-emailing across segments, campaigns, and Clay runs.

Each line is one enrollment record:
{
    "email": "jane@example.com",
    "company": "Example Corp",
    "sequence_id": "...",
    "campaign_id": "icp-2026-02-15-ce-cred-v1",
    "seed_set_id": "...",
    "enrolled_at": "2026-02-15T21:30:00Z",
    "variables_hash": "..."
}
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LEDGER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "mixmax_sent_ledger.jsonl"
)


def _ensure_data_dir():
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)


def _load_all_records():
    """Load all ledger records. Returns list of dicts."""
    if not os.path.exists(LEDGER_PATH):
        return []
    records = []
    with open(LEDGER_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed ledger line")
    return records


def is_enrolled(email):
    """
    Check if email exists in sent ledger.

    Returns the most recent enrollment record if found, None otherwise.
    """
    email_lower = email.strip().lower()
    records = _load_all_records()
    match = None
    for rec in records:
        if rec.get("email", "").lower() == email_lower:
            match = rec  # keep last (most recent) match
    return match


def record_enrollment(email, company, sequence_id, campaign_id="",
                      seed_set_id="", variables=None):
    """
    Append an enrollment record to the ledger.

    Called after successful Mixmax API enrollment (not during prepare).
    """
    _ensure_data_dir()
    variables_hash = ""
    if variables:
        serialized = json.dumps(variables, sort_keys=True)
        variables_hash = hashlib.sha256(serialized.encode()).hexdigest()[:12]

    record = {
        "email": email.strip().lower(),
        "company": company,
        "sequence_id": sequence_id,
        "campaign_id": campaign_id,
        "seed_set_id": seed_set_id,
        "enrolled_at": datetime.now(timezone.utc).isoformat(),
        "variables_hash": variables_hash,
    }

    with open(LEDGER_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")

    logger.info(f"Ledger: recorded enrollment for {email}")
    return record


def get_enrollment_history(limit=100):
    """
    Return recent enrollment records for the UI.

    Returns most recent first, capped at limit.
    """
    records = _load_all_records()
    records.reverse()  # most recent first
    return records[:limit]


def get_enrolled_emails():
    """Return set of all enrolled email addresses (lowercase)."""
    records = _load_all_records()
    return {rec.get("email", "").lower() for rec in records if rec.get("email")}


def get_ledger_stats():
    """Return summary stats for the UI."""
    records = _load_all_records()
    emails = set()
    companies = set()
    sequences = set()
    for rec in records:
        emails.add(rec.get("email", "").lower())
        companies.add(rec.get("company", ""))
        sequences.add(rec.get("sequence_id", ""))
    return {
        "total_enrollments": len(records),
        "unique_contacts": len(emails),
        "unique_companies": len(companies - {""}),
        "sequences_used": len(sequences - {""}),
    }
