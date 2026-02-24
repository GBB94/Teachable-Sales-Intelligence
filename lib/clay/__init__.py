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


def _load_dashboard_data(data=None):
    """Load canonical dashboard data for snapshot generation.

    Priority order:
      1. Passed-in data dict (from server.py or direct call)
      2. test_output/features.json (canonical machine-readable dataset)
      3. test_output/index.html DATA blob (let/const supported)
      4. HARD FAIL — never fall back to demo/preview files
    """
    if data is not None:
        logger.info("[snapshot] Using passed-in data dict")
        return data

    base_dir = os.path.dirname(os.path.abspath(__file__))
    features_path = os.path.join(base_dir, "..", "..", "test_output", "features.json")
    if os.path.exists(features_path):
        try:
            with open(features_path, "r") as f:
                data = json.load(f)
            # Minimal schema check to avoid loading accidental junk
            if isinstance(data, dict) and "calls" in data and "mentions" in data:
                logger.info("[snapshot] Loading from %s", features_path)
                return data
        except Exception:
            pass

    html_path = os.path.join(base_dir, "..", "..", "test_output", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r") as f:
            html = f.read()
        for start_marker in ("let DATA = ", "const DATA = "):
            idx = html.find(start_marker)
            if idx == -1:
                continue
            json_start = idx + len(start_marker)
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(html, json_start)
                logger.info("[snapshot] Parsing from %s", html_path)
                return data
            except json.JSONDecodeError:
                continue

    raise FileNotFoundError(
        "Cannot generate snapshot: no live data found. "
        "Expected test_output/features.json or test_output/index.html with DATA object. "
        "Run 'python3 analyze_features.py inject' first."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
_DEMO_COMPANY_NAMES = {"demo corp", "example inc", "test company", "acme corp", "sample org"}


def _validate_snapshot(snapshot):
    """Sanity-check a generated snapshot. Returns list of warning strings (empty = OK)."""
    warnings = []

    seeds = snapshot.get("seed_companies", [])
    if not seeds:
        warnings.append("Snapshot has zero seed companies")
        return warnings

    # Check for demo-data contamination
    names = {s.get("company_name", "").lower() for s in seeds}
    demo_hits = names & _DEMO_COMPANY_NAMES
    if demo_hits:
        warnings.append(f"Demo company names detected in seeds: {demo_hits}")

    # Check for degenerate scores (all identical)
    scores = [s.get("score", 0) for s in seeds]
    if len(set(scores)) == 1 and len(scores) > 1:
        warnings.append(f"All {len(scores)} seeds have identical score ({scores[0]})")

    # Check for missing segments
    no_segment = sum(1 for s in seeds if not s.get("segment"))
    if no_segment == len(seeds):
        warnings.append("No seeds have segment assignments")

    return warnings


# ---------------------------------------------------------------------------
# Primary workflow
# ---------------------------------------------------------------------------
def generate_snapshot(data=None):
    """
    Generate an ICP Snapshot from current dashboard data.
    Does NOT push anything to Clay. Returns the full snapshot.

    If data dict is passed, use it directly.
    If not, fall back to _load_dashboard_data() which raises FileNotFoundError
    when no live data is available.
    """
    global _current_snapshot

    if data is None:
        data = _load_dashboard_data()

    snapshot = generate_icp_snapshot(data)

    # Validate before caching
    warnings = _validate_snapshot(snapshot)
    for w in warnings:
        logger.warning("[snapshot] %s", w)

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
