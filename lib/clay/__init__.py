"""
Clay.com v3 integration module — public API.

Oriented around ICP Snapshots, seed companies, exclude lists, and
re-engagement detection. Only this module should be imported by server.py.
"""

import json
import logging
import os
import re

from .client import ClayClient
from .scoring import calculate_score, get_config, reload_config as _reload_config
from .transforms import (
    aggregate_companies,
    generate_icp_snapshot,
    generate_seed_payloads,
    generate_exclude_payloads,
    generate_re_engagement_payloads,
    load_last_snapshot,
)

logger = logging.getLogger(__name__)

# Lazy-initialized singleton
_client = None
# In-memory snapshot cache (latest generated snapshot)
_current_snapshot = None


def _get_client():
    global _client
    if _client is None:
        _client = ClayClient()
    return _client


def _get_webhook_url(entity_type):
    """Get webhook URL from environment. entity_type: 'seed', 'exclude', 'reengagement'."""
    mapping = {
        "seed": "CLAY_WEBHOOK_SEEDS",
        "exclude": "CLAY_WEBHOOK_EXCLUDES",
        "reengagement": "CLAY_WEBHOOK_REENGAGEMENT",
    }
    key = mapping.get(entity_type, f"CLAY_WEBHOOK_{entity_type.upper()}S")
    return os.environ.get(key, "")


def _load_dashboard_data():
    """Load DATA from the rendered dashboard HTML."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(base_dir, "..", "..", "test_output", "index.html"),
        os.path.join(base_dir, "..", "..", "dashboard_preview.html"),
        os.path.join(base_dir, "..", "..", "dashboard_latest.html"),
    ]
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, "r") as f:
            html = f.read()
        start_marker = "const DATA = "
        idx = html.find(start_marker)
        if idx == -1:
            continue
        json_start = idx + len(start_marker)
        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(html, json_start)
            return data
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Primary workflow
# ---------------------------------------------------------------------------
def generate_snapshot(data=None):
    """
    Generate an ICP Snapshot from current dashboard data.
    Does NOT push anything to Clay. Returns the full snapshot.

    If data dict is passed, use it directly.
    If not, fall back to _load_dashboard_data() (parses HTML from disk).
    """
    global _current_snapshot

    if data is None:
        data = _load_dashboard_data()
    if data is None:
        return {"error": "No dashboard data found. Run a scan first."}

    snapshot = generate_icp_snapshot(data)
    _current_snapshot = snapshot
    return snapshot


def export_seeds(seed_ids=None):
    """
    Push seed companies to Clay. Optionally filter to specific seed IDs.

    This is the credit commitment. If Ocean.io auto-run is enabled in Clay,
    credits start burning the moment seeds hit the webhook.

    Returns: {success, sent, credit_estimate, errors}
    """
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"success": False, "sent": 0, "errors": [{"error": "No snapshot generated. POST /api/clay/snapshot first."}]}

    url = _get_webhook_url("seed")
    if not url:
        return {
            "success": False,
            "sent": 0,
            "errors": [{"error": "CLAY_WEBHOOK_SEEDS not configured. Set it in .env and restart."}],
            "credit_estimate": snapshot.get("estimated_credit_impact"),
        }

    payloads = generate_seed_payloads(snapshot)

    # Filter to specific seed IDs if requested
    if seed_ids:
        seed_set = set(seed_ids)
        payloads = [p for p in payloads if p.get("company_id") in seed_set]

    client = _get_client()
    result = client.send_batch(url, payloads)

    return {
        "success": result["success"],
        "sent": result["sent"],
        "failed": result["failed"],
        "credit_estimate": snapshot.get("estimated_credit_impact"),
        "errors": result["errors"],
    }


def export_exclude_list():
    """
    Push exclude list to Clay via chunked sending.
    Reads pre-computed excludes from the snapshot (no HTML re-parsing).

    Returns: {success, sent, chunks, omitted_no_domain, errors}
    """
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"success": False, "sent": 0, "chunks": 0, "omitted_no_domain": 0,
                "errors": [{"error": "No snapshot generated. POST /api/clay/snapshot first."}]}

    url = _get_webhook_url("exclude")
    if not url:
        return {
            "success": False,
            "sent": 0,
            "chunks": 0,
            "omitted_no_domain": 0,
            "errors": [{"error": "CLAY_WEBHOOK_EXCLUDES not configured. Set it in .env and restart."}],
        }

    payloads = snapshot.get("exclude_list", [])
    omitted = snapshot.get("exclude_omitted", [])

    if not payloads:
        return {
            "success": True,
            "sent": 0,
            "chunks": 0,
            "omitted_no_domain": len(omitted),
            "omitted_companies": omitted,
            "errors": [],
            "message": f"No excludes to send. {len(omitted)} companies omitted (no domain or LinkedIn URL).",
        }

    client = _get_client()
    result = client.send_chunked(url, payloads)

    return {
        "success": result["success"],
        "sent": result["total_sent"],
        "chunks": result["chunks_sent"],
        "omitted_no_domain": len(omitted),
        "omitted_companies": omitted,
        "errors": result["errors"],
    }


def export_re_engagements():
    """
    Push re-engagement alerts to Clay.

    Returns: {success, sent, errors}
    """
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"success": False, "sent": 0, "errors": [{"error": "No snapshot generated. POST /api/clay/snapshot first."}]}

    alerts = snapshot.get("re_engagement_alerts", [])
    if not alerts:
        return {"success": True, "sent": 0, "errors": [], "message": "No re-engagement alerts in current snapshot."}

    url = _get_webhook_url("reengagement")
    if not url:
        return {"success": False, "sent": 0, "errors": [{"error": "CLAY_WEBHOOK_REENGAGEMENT not configured."}]}

    payloads = generate_re_engagement_payloads(snapshot)
    client = _get_client()
    result = client.send_batch(url, payloads)

    return {
        "success": result["success"],
        "sent": result["sent"],
        "failed": result["failed"],
        "errors": result["errors"],
    }


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def get_snapshot():
    """Return latest generated snapshot without re-generating."""
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"error": "No snapshot generated yet. POST /api/clay/snapshot first."}
    # Strip internal fields
    return {k: v for k, v in snapshot.items() if not k.startswith("_")}


def get_seed_companies():
    """
    Return proposed seed companies in a human-readable format.
    Designed for review before export.
    """
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"error": "No snapshot generated yet. POST /api/clay/snapshot first."}

    seeds = snapshot.get("seed_companies", [])
    readable = []
    for i, s in enumerate(seeds, 1):
        readable.append({
            "rank": i,
            "company": s["company_name"],
            "company_id": s.get("company_id", ""),
            "domain": s.get("domain", "") or "(unknown)",
            "domain_confidence": s.get("domain_confidence", "unresolved"),
            "segment": s.get("segment", "") or "(unassigned)",
            "score": s.get("score", 0),
            "call_count": s.get("call_count", 0),
            "features": s.get("features_requested", []),
            "previously_exported": s.get("previously_exported", False),
        })

    return {
        "seed_set_id": snapshot.get("seed_set_id", ""),
        "seed_count": len(seeds),
        "credit_estimate": snapshot.get("estimated_credit_impact", {}).get("total_estimated", 0),
        "seeds": readable,
    }


def get_segment_analysis():
    """Return segment performance breakdown from latest snapshot."""
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"error": "No snapshot generated yet."}
    return {"segments": snapshot.get("segments", [])}


def get_feature_rankings():
    """Return feature demand rankings from latest snapshot."""
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"error": "No snapshot generated yet."}
    return {"features": snapshot.get("feature_rankings", [])}


def get_competitor_landscape():
    """Return competitor landscape from latest snapshot."""
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"error": "No snapshot generated yet."}
    return {"competitors": snapshot.get("competitor_landscape", [])}


def get_re_engagements():
    """Return re-engagement alerts from latest snapshot."""
    snapshot = _current_snapshot or load_last_snapshot()
    if not snapshot:
        return {"error": "No snapshot generated yet."}
    return {"alerts": snapshot.get("re_engagement_alerts", [])}


def reload_config():
    """Hot-reload scoring and snapshot config."""
    return _reload_config()
