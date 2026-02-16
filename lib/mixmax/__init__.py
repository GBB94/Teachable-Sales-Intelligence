"""
Mixmax integration module for intelligence-driven email campaigns.

Public API:
    get_sequences()          — List available Mixmax sequences
    prepare_enrollment()     — Validate, dedup, map variables. Returns preview. No side effects.
    enroll_contacts()        — Push a prepared batch to Mixmax. Requires prepared_id.
    get_enrollment_history() — Recent enrollment records from sent ledger.
    reload_config()          — Reload config.json from disk.
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone

from .client import MixmaxClient
from .mapper import map_contact_to_variables
from .ledger import (
    is_enrolled,
    record_enrollment,
    get_enrollment_history as _ledger_history,
    get_ledger_stats,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

_config = None
_prepared_batches = {}  # prepared_id -> batch data


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_config():
    global _config
    with open(CONFIG_PATH, "r") as f:
        _config = json.load(f)
    return _config


def get_config():
    global _config
    if _config is None:
        _load_config()
    return _config


def reload_config():
    return _load_config()


# ---------------------------------------------------------------------------
# Sequence resolution
# ---------------------------------------------------------------------------
def _resolve_sequence_id(segment, override_sequence_id=None):
    """
    Determine which Mixmax sequence to use.

    Priority:
    1. Explicit override_sequence_id (passed by caller)
    2. Segment-specific sequence from config
    3. default_sequence_id from config
    4. fallback_sequence_id from config
    """
    cfg = get_config()
    if override_sequence_id:
        return override_sequence_id

    seg_sequences = cfg.get("segment_sequences", {})
    seq_id = seg_sequences.get(segment, "")
    if seq_id:
        return seq_id

    return cfg.get("default_sequence_id", "") or cfg.get("fallback_sequence_id", "")


# ---------------------------------------------------------------------------
# Public API: get_sequences
# ---------------------------------------------------------------------------
def get_sequences():
    """List available Mixmax sequences."""
    client = MixmaxClient()
    if not client.api_token:
        return {"error": "MIXMAX_API_TOKEN not configured"}
    try:
        result = client.list_sequences()
        sequences = result.get("results", result) if isinstance(result, dict) else result
        if isinstance(sequences, list):
            return {
                "sequences": [
                    {
                        "_id": s.get("_id", ""),
                        "name": s.get("name", ""),
                        "numStages": s.get("numStages", 0),
                    }
                    for s in sequences
                ],
                "count": len(sequences),
            }
        return {"sequences": [], "count": 0}
    except Exception as e:
        logger.error(f"Failed to list sequences: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Public API: prepare_enrollment
# ---------------------------------------------------------------------------
def prepare_enrollment(contacts, seed_intelligence, enrichment_facts=None,
                       sequence_id=None, campaign_id=None):
    """
    Phase 1: Prepare and validate. No emails sent. No side effects.

    Parameters
    ----------
    contacts : list[dict]
        Enriched contacts from Clay. Each must have at least 'email'.
        {email, first_name, last_name, title, company, domain}

    seed_intelligence : dict
        From the dashboard snapshot (the seed that generated these lookalikes).
        {segment, features_requested, competitors_mentioned, score}

    enrichment_facts : dict or list[dict], optional
        If dict: applied to all contacts (global enrichment).
        If list: per-contact enrichment, same length as contacts.

    sequence_id : str, optional
        Override sequence ID. Otherwise resolved from segment.

    campaign_id : str, optional
        Human-readable campaign identifier for ledger tracking.

    Returns
    -------
    dict with prepared_id, ready, skipped_duplicate, skipped_no_email, warnings.
    """
    cfg = get_config()
    segment = seed_intelligence.get("segment", "")
    resolved_seq = _resolve_sequence_id(segment, sequence_id)

    if not campaign_id:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        seg_slug = segment.lower().replace(" ", "-").replace("&", "and")[:30] if segment else "general"
        campaign_id = f"icp-{date_str}-{seg_slug}"

    ready = []
    skipped_duplicate = []
    skipped_no_email = []
    warnings = []

    if not resolved_seq:
        warnings.append(f"No sequence configured for segment '{segment}'. Set sequence IDs in config.json.")

    for i, contact in enumerate(contacts):
        email = (contact.get("email") or "").strip().lower()

        # No email — skip
        if not email:
            skipped_no_email.append({
                "company": contact.get("company", "(unknown)"),
                "name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
                "reason": "No email address",
            })
            continue

        # Dedup check against sent ledger
        if cfg.get("dedup_check", True):
            prev = is_enrolled(email)
            if prev:
                skipped_duplicate.append({
                    "email": email,
                    "company": contact.get("company", ""),
                    "reason": "Previously enrolled",
                    "previously_enrolled_at": prev.get("enrolled_at", ""),
                    "previous_sequence_id": prev.get("sequence_id", ""),
                })
                continue

        # Per-contact or global enrichment facts
        facts = None
        if isinstance(enrichment_facts, list) and i < len(enrichment_facts):
            facts = enrichment_facts[i]
        elif isinstance(enrichment_facts, dict):
            facts = enrichment_facts

        # Map variables
        variables = map_contact_to_variables(contact, seed_intelligence, facts)

        ready.append({
            "email": email,
            "company": contact.get("company", ""),
            "variables": variables,
            "sequence_id": resolved_seq,
        })

    # Generate prepared_id and store for enroll phase
    prepared_id = secrets.token_hex(16)
    _prepared_batches[prepared_id] = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": campaign_id,
        "seed_set_id": seed_intelligence.get("seed_set_id", ""),
        "segment": segment,
        "sequence_id": resolved_seq,
        "ready": ready,
        "dry_run": cfg.get("dry_run", True),
    }

    return {
        "prepared_id": prepared_id,
        "campaign_id": campaign_id,
        "sequence_id": resolved_seq,
        "segment": segment,
        "dry_run": cfg.get("dry_run", True),
        "ready": ready,
        "skipped_duplicate": skipped_duplicate,
        "skipped_no_email": skipped_no_email,
        "warnings": warnings,
        "total_ready": len(ready),
        "total_skipped": len(skipped_duplicate) + len(skipped_no_email),
    }


# ---------------------------------------------------------------------------
# Public API: enroll_contacts
# ---------------------------------------------------------------------------
def enroll_contacts(prepared_id):
    """
    Phase 2: Actually enroll contacts via Mixmax API.

    Requires a prepared_id from prepare_enrollment().
    Respects dry_run config. Writes to sent ledger on success.

    Returns
    -------
    dict with enrolled, failed, errors, dry_run.
    """
    batch = _prepared_batches.get(prepared_id)
    if not batch:
        return {"error": f"Unknown prepared_id: {prepared_id}. Run prepare first."}

    ready = batch["ready"]
    if not ready:
        return {"error": "No contacts to enroll in this batch."}

    sequence_id = batch["sequence_id"]
    if not sequence_id:
        return {"error": "No sequence_id configured. Set sequence IDs in config.json."}

    campaign_id = batch["campaign_id"]
    seed_set_id = batch.get("seed_set_id", "")
    dry_run = batch.get("dry_run", True)

    if dry_run:
        # Log what would happen, but don't call the API
        logger.info(f"[DRY RUN] Would enroll {len(ready)} contacts into sequence {sequence_id}")
        return {
            "enrolled": len(ready),
            "failed": 0,
            "errors": [],
            "dry_run": True,
            "message": f"Dry run: {len(ready)} contacts would be enrolled. Set dry_run=false in config to send.",
        }

    # Real enrollment
    client = MixmaxClient()
    if not client.api_token:
        return {"error": "MIXMAX_API_TOKEN not configured"}

    enrolled = 0
    failed = 0
    errors = []

    # Send in batches to stay under rate limits
    for entry in ready:
        try:
            client.add_recipient(
                sequence_id,
                entry["email"],
                variables=entry["variables"],
            )
            record_enrollment(
                email=entry["email"],
                company=entry["company"],
                sequence_id=sequence_id,
                campaign_id=campaign_id,
                seed_set_id=seed_set_id,
                variables=entry["variables"],
            )
            enrolled += 1
        except Exception as e:
            failed += 1
            errors.append({
                "email": entry["email"],
                "error": str(e),
            })
            logger.error(f"Failed to enroll {entry['email']}: {e}")

    # Clean up prepared batch
    del _prepared_batches[prepared_id]

    return {
        "enrolled": enrolled,
        "failed": failed,
        "errors": errors,
        "dry_run": False,
        "sequence_id": sequence_id,
        "campaign_id": campaign_id,
    }


# ---------------------------------------------------------------------------
# Public API: history + stats
# ---------------------------------------------------------------------------
def get_enrollment_history(limit=100):
    """Return recent enrollment records from the sent ledger."""
    return {
        "records": _ledger_history(limit),
        "stats": get_ledger_stats(),
    }
