#!/usr/bin/env python3
"""
Local dev server for Teachable Sales Intelligence.
Serves the dashboard and exposes a two-step scan API:
  GET  /api/scan/preview  — fetch calls from Fireflies, return list for approval
  POST /api/scan/process  — add selected calls to dashboard (pending analysis)

Usage:
    python3 server.py
"""

import json
import os
import re
import secrets
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone

# Ensure repo root is on the import path so fireflies_retriever is found
# regardless of where the script is invoked from.
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_DIR)

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from fireflies_retriever import FirefliesRetriever, CallFilter
from models import DEFAULT_SCAN_TITLE_KEYWORDS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCAN_DAYS = 14
SCAN_LIMIT = 50
SCAN_TITLE_KEYWORDS = DEFAULT_SCAN_TITLE_KEYWORDS
SCAN_OWNERS = ['zach.mccall', 'jerome.olaloye', 'kevin.codde']
SCAN_EXCLUDE_DOMAINS = ['teachable.com']
PORT = 8080

TEMPLATE_PATH = os.path.join(REPO_DIR, 'dashboard_template.html')
OUTPUT_DIR = os.path.join(REPO_DIR, 'test_output')
OUTPUT_PATH = os.path.join(OUTPUT_DIR, 'index.html')

app = Flask(__name__)

# In-memory cache of the last preview fetch so /process can use the Call objects
# without re-fetching from Fireflies.
_preview_cache = {}  # call_id -> Call object
_clay_tokens = {}    # token -> expiry timestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean_expired_tokens():
    """Remove expired tokens from the store."""
    now = time.time()
    expired = [t for t, exp in _clay_tokens.items() if exp < now]
    for t in expired:
        del _clay_tokens[t]


def _require_clay_token():
    """Validate Authorization: Bearer header. Returns None if valid, or (response, 401)."""
    _clean_expired_tokens()
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({"error": "Missing or invalid Authorization header"}), 401
    token = auth[7:]
    if token not in _clay_tokens:
        return jsonify({"error": "Invalid or expired token"}), 401
    return None
def _empty_data():
    return {
        "stats": {
            "total_mentions": 0,
            "unique_calls": 0,
            "unique_features": 0,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "mentions": [],
        "calls": [],
    }


def _extract_data_from_html(path):
    """Extract the DATA JSON from a rendered dashboard HTML file."""
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        html = f.read()
    # Use JSONDecoder for robust parsing of large embedded JSON
    start_marker = "let DATA = "
    idx = html.find(start_marker)
    if idx == -1:
        # Fallback for older dashboards that used const
        start_marker = "const DATA = "
        idx = html.find(start_marker)
    if idx == -1:
        return None
    json_start = idx + len(start_marker)
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(html, json_start)
    return data


def _load_existing_data():
    """Load DATA JSON from test_output/index.html."""
    data = _extract_data_from_html(OUTPUT_PATH)
    if data:
        return data
    return _empty_data()


def _render_dashboard(data):
    """Read the template and inject DATA JSON, including prospecting snapshot."""
    # Bake prospecting snapshot into DATA so Netlify static deploy works
    try:
        from lib.clay import get_snapshot, get_seed_companies
        snap = get_snapshot()
        if snap and "error" not in snap:
            data["prospecting_snapshot"] = snap
        seeds = get_seed_companies()
        if seeds and "error" not in seeds:
            data["prospecting_seeds"] = seeds
    except Exception:
        pass  # Snapshot not available, Prospecting tab will show empty state
    with open(TEMPLATE_PATH, 'r') as f:
        template = f.read()
    return template.replace('{{DATA_JSON}}', json.dumps(data))


def _save_dashboard(data):
    """Save a rendered dashboard HTML to test_output/index.html."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html = _render_dashboard(data)
    with open(OUTPUT_PATH, 'w') as f:
        f.write(html)


def _call_to_dict(call):
    """Convert a Call object to the dashboard data format, marked pending."""
    return {
        "id": call.id,
        "title": call.title,
        "date": call.date[:10] if call.date else "",
        "duration": round(call.duration_minutes),
        "organizer": call.organizer_email or "",
        "attendees": ", ".join(call.attendee_names) or "",
        "transcript": call.transcript_url or "",
        "hubspot_note": call.to_hubspot_note(),
        "transcript_text": call.full_transcript_text or "",
        "pending_analysis": True,
    }


def _merge_calls(existing_data, new_calls_data):
    """Merge new call dicts into existing data, deduplicating by ID.

    Only adds calls that don't already exist. Preserves existing mentions
    and stats untouched — features come from analyze_features.py, not here.
    """
    existing_call_ids = {c["id"] for c in existing_data.get("calls", [])}
    merged_calls = list(existing_data.get("calls", []))
    added = 0

    for c in new_calls_data:
        if c["id"] not in existing_call_ids:
            merged_calls.append(c)
            existing_call_ids.add(c["id"])
            added += 1

    result = dict(existing_data)
    result["calls"] = merged_calls
    # Keep existing stats/mentions unchanged — analysis updates those
    return result, added


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    data = _load_existing_data()
    return _render_dashboard(data)


@app.route('/api/scan/preview')
def scan_preview():
    """Step 1: Fetch calls from Fireflies and return list for user approval."""
    global _preview_cache

    api_key = os.getenv('FIREFLIES_API_KEY')
    if not api_key:
        return jsonify({"error": "FIREFLIES_API_KEY not set"}), 500

    try:
        retriever = FirefliesRetriever(api_key)

        filt = CallFilter(
            days_back=SCAN_DAYS,
            limit=SCAN_LIMIT,
            title_keywords=SCAN_TITLE_KEYWORDS,
            owner_emails=SCAN_OWNERS,
            bypass_keywords_owners=['kevin.codde'],
        )

        print(f"[preview] Fetching calls (days_back={SCAN_DAYS}, limit={SCAN_LIMIT}, owners={SCAN_OWNERS})...")
        calls = retriever.get_calls(filter_criteria=filt, verbose=True)
        print(f"[preview] Found {len(calls)} calls")

        # Cache for /process
        _preview_cache = {call.id: call for call in calls}

        # Check which calls already exist in the dashboard
        existing = _load_existing_data()
        existing_call_ids = {c["id"] for c in existing.get("calls", [])}

        preview = []
        for call in calls:
            preview.append({
                "id": call.id,
                "title": call.title,
                "date": call.date[:10] if call.date else "",
                "duration": round(call.duration_minutes),
                "organizer": call.organizer_email or "",
                "attendees": ", ".join(call.attendee_names) or "",
                "transcript": call.transcript_url or "",
                "already_exists": call.id in existing_call_ids,
            })

        return jsonify({"calls": preview})

    except Exception as e:
        print(f"[preview] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/scan/process', methods=['POST'])
def scan_process():
    """Step 2: Add selected calls to dashboard as pending analysis."""
    global _preview_cache

    body = request.get_json(force=True)
    selected_ids = body.get("call_ids", [])

    if not selected_ids:
        return jsonify({"error": "No calls selected"}), 400

    # Get Call objects from cache
    selected_calls = [_preview_cache[cid] for cid in selected_ids if cid in _preview_cache]

    if not selected_calls:
        return jsonify({"error": "Selected calls not found in preview cache. Run preview again."}), 400

    print(f"[process] Adding {len(selected_calls)} calls: {[c.title for c in selected_calls]}")

    try:
        # Convert Call objects to dashboard data format
        new_calls_data = [_call_to_dict(c) for c in selected_calls]

        # Merge with existing (dedup by ID)
        existing = _load_existing_data()
        merged, added = _merge_calls(existing, new_calls_data)

        # Save to disk
        _save_dashboard(merged)
        print(f"[process] Done. Added {added} new calls ({len(merged['calls'])} total).")

        return jsonify(merged)

    except Exception as e:
        print(f"[process] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/analysis/extract', methods=['POST'])
def analysis_extract():
    """Step 1: Run extract to generate the analysis prompt for pending calls."""
    try:
        result = subprocess.run(
            ['python3', 'analyze_features.py', 'extract', OUTPUT_PATH],
            capture_output=True, text=True, timeout=30, cwd=REPO_DIR
        )
        output = result.stdout
        pending = 0
        for line in output.split('\n'):
            if 'need analysis' in line:
                m = re.search(r'(\d+) need analysis', line)
                if m:
                    pending = int(m.group(1))
                break
        return jsonify({"prompt": output, "pending_count": pending})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/analysis/inject', methods=['POST'])
def analysis_inject():
    """Step 2: Validate, then inject Claude's analysis JSON."""
    import tempfile

    body = request.get_json(force=True)
    if not body:
        return jsonify({"error": "No JSON body provided"}), 400

    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, prefix='analysis_'
    )
    json.dump(body, tmp)
    tmp.close()

    try:
        # Validate BEFORE inject so bad data doesn't get merged
        validate_result = subprocess.run(
            ['python3', 'analyze_features.py', 'validate', tmp.name],
            capture_output=True, text=True, timeout=30, cwd=REPO_DIR
        )
        # Inject
        inject_result = subprocess.run(
            ['python3', 'analyze_features.py', 'inject', OUTPUT_PATH, tmp.name],
            capture_output=True, text=True, timeout=60, cwd=REPO_DIR
        )
        # Reload fresh data
        data = _load_existing_data()
        return jsonify({
            "success": inject_result.returncode == 0,
            "inject_output": inject_result.stdout + inject_result.stderr,
            "validate_output": validate_result.stdout + validate_result.stderr,
            "data": data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp.name)


@app.route('/api/exclude-competitor', methods=['POST'])
def exclude_competitor():
    """Add or remove a competitor from the exclusion list."""
    body = request.get_json(force=True)
    name = body.get("name", "").strip()
    action = body.get("action", "exclude")  # "exclude" or "restore"

    if not name:
        return jsonify({"error": "Missing competitor name"}), 400

    try:
        data = _load_existing_data()
        excluded = data.get("excluded_competitors", [])

        if action == "exclude":
            if name not in excluded:
                excluded.append(name)
        elif action == "restore":
            excluded = [n for n in excluded if n != name]

        data["excluded_competitors"] = excluded
        _save_dashboard(data)

        # Also update features.json
        features_path = os.path.join(OUTPUT_DIR, 'features.json')
        if os.path.exists(features_path):
            with open(features_path, 'r') as f:
                fdata = json.load(f)
            fdata["excluded_competitors"] = excluded
            with open(features_path, 'w') as f:
                json.dump(fdata, f, indent=2)

        print(f"[exclude-competitor] {action}: {name} (total excluded: {len(excluded)})")
        return jsonify({"status": "ok", "excluded_competitors": excluded})

    except Exception as e:
        print(f"[exclude-competitor] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/sync-sheets', methods=['POST'])
def sync_sheets():
    """Trigger a sheet sync from the dashboard UI."""
    try:
        from sync_to_sheets import sync
        result = sync(output_dir=OUTPUT_DIR)
        return jsonify({
            "status": "ok",
            "rows_added": result["rows_added"],
            "rows_updated": result["rows_updated"],
        })
    except FileNotFoundError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        print(f"[sync-sheets] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Clay.com Integration (v3 — ICP Intelligence)
# ---------------------------------------------------------------------------

@app.route('/api/clay/verify-password', methods=['POST'])
def clay_verify_password():
    """Verify the Clay dashboard password and issue a session token."""
    expected = os.getenv('CLAY_DASHBOARD_PASSWORD', '')
    if not expected:
        return jsonify({"valid": False, "error": "No password configured"}), 503
    body = request.get_json(force=True)
    password = body.get("password", "")
    if password != expected:
        return jsonify({"valid": False}), 401
    _clean_expired_tokens()
    token = secrets.token_hex(32)
    _clay_tokens[token] = time.time() + 3600  # 1 hour TTL
    return jsonify({"valid": True, "token": token}), 200

# --- Generate and inspect ---

@app.route('/api/clay/snapshot', methods=['POST'])
def clay_generate_snapshot():
    auth_err = _require_clay_token()
    if auth_err:
        return auth_err
    from lib.clay import generate_snapshot
    # Generate from the same DATA source the dashboard is currently serving.
    # This avoids stale preview/demo file fallbacks and keeps snapshot + UI aligned.
    data = _load_existing_data()
    result = generate_snapshot(data=data)
    if "error" in result:
        return jsonify(result), 500
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    return jsonify(clean), 200


@app.route('/api/clay/snapshot', methods=['GET'])
def clay_get_snapshot():
    from lib.clay import get_snapshot
    result = get_snapshot()
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


@app.route('/api/clay/segments', methods=['GET'])
def clay_segments():
    from lib.clay import get_segment_analysis
    result = get_segment_analysis()
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


@app.route('/api/clay/features', methods=['GET'])
def clay_features():
    from lib.clay import get_feature_rankings
    result = get_feature_rankings()
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


@app.route('/api/clay/competitors', methods=['GET'])
def clay_competitors():
    from lib.clay import get_competitor_landscape
    result = get_competitor_landscape()
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


@app.route('/api/clay/seeds', methods=['GET'])
def clay_seeds():
    from lib.clay import get_seed_companies
    result = get_seed_companies()
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


@app.route('/api/clay/reengagements', methods=['GET'])
def clay_reengagements():
    from lib.clay import get_re_engagements
    result = get_re_engagements()
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result), 200


# --- Export (push to Clay) ---

@app.route('/api/clay/export-seeds', methods=['POST'])
def clay_export_seeds():
    auth_err = _require_clay_token()
    if auth_err:
        return auth_err
    from lib.clay import export_seeds
    body = request.get_json(silent=True) or {}
    seed_ids = body.get("seed_ids")
    result = export_seeds(seed_ids=seed_ids)
    status_code = 200 if result.get("success") else 400
    if result.get("errors") and any("not configured" in str(e.get("error", "")).lower() for e in result["errors"]):
        status_code = 503
    return jsonify(result), status_code


@app.route('/api/clay/export-excludes', methods=['POST'])
def clay_export_excludes():
    auth_err = _require_clay_token()
    if auth_err:
        return auth_err
    from lib.clay import export_exclude_list
    result = export_exclude_list()
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@app.route('/api/clay/export-reengagements', methods=['POST'])
def clay_export_reengagements():
    auth_err = _require_clay_token()
    if auth_err:
        return auth_err
    from lib.clay import export_re_engagements
    result = export_re_engagements()
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


# --- Utilities ---

@app.route('/api/clay/test-connectivity', methods=['POST'])
def clay_test_connectivity():
    from lib.clay.client import ClayClient
    client = ClayClient()
    results = {}
    for entity_type, env_key in [("seeds", "CLAY_WEBHOOK_SEEDS"), ("excludes", "CLAY_WEBHOOK_EXCLUDES"), ("reengagement", "CLAY_WEBHOOK_REENGAGEMENT")]:
        url = os.getenv(env_key, "")
        if url:
            results[entity_type] = client.test_connectivity(url)
        else:
            results[entity_type] = {"reachable": False, "error": f"{env_key} not configured"}
    return jsonify(results), 200


@app.route('/api/clay/reload-config', methods=['POST'])
def clay_reload_config():
    from lib.clay import reload_config
    from lib.clay.scoring import get_config
    reload_config()
    return jsonify({"message": "Config reloaded", "config": get_config()}), 200


# ---------------------------------------------------------------------------
# Mixmax Integration (intelligence-driven email campaigns)
# ---------------------------------------------------------------------------

@app.route('/api/mixmax/sequences', methods=['GET'])
def mixmax_sequences():
    """List available Mixmax sequences."""
    from lib.mixmax import get_sequences
    result = get_sequences()
    if "error" in result:
        return jsonify(result), 503
    return jsonify(result), 200


@app.route('/api/mixmax/prepare', methods=['POST'])
def mixmax_prepare():
    """Prepare enrollment: validate, dedup, map variables. No side effects."""
    from lib.mixmax import prepare_enrollment
    body = request.get_json(force=True)
    contacts = body.get("contacts", [])
    seed_intelligence = body.get("seed_intelligence", {})
    enrichment_facts = body.get("enrichment_facts")
    sequence_id = body.get("sequence_id")
    campaign_id = body.get("campaign_id")
    if not contacts:
        return jsonify({"error": "No contacts provided"}), 400
    if not seed_intelligence:
        return jsonify({"error": "No seed_intelligence provided"}), 400
    result = prepare_enrollment(
        contacts=contacts,
        seed_intelligence=seed_intelligence,
        enrichment_facts=enrichment_facts,
        sequence_id=sequence_id,
        campaign_id=campaign_id,
    )
    return jsonify(result), 200


@app.route('/api/mixmax/enroll', methods=['POST'])
def mixmax_enroll():
    """Enroll a prepared batch into Mixmax. Requires auth token."""
    auth_err = _require_clay_token()
    if auth_err:
        return auth_err
    from lib.mixmax import enroll_contacts
    body = request.get_json(force=True)
    prepared_id = body.get("prepared_id", "")
    if not prepared_id:
        return jsonify({"error": "Missing prepared_id"}), 400
    result = enroll_contacts(prepared_id)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


@app.route('/api/mixmax/history', methods=['GET'])
def mixmax_history():
    """Return enrollment history from the sent ledger."""
    from lib.mixmax import get_enrollment_history
    limit = request.args.get("limit", 100, type=int)
    result = get_enrollment_history(limit=limit)
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    load_dotenv(os.path.join(REPO_DIR, '.env'))

    print(f"Starting dashboard server on http://localhost:{PORT}")
    print(f"Repo:     {REPO_DIR}")
    print(f"Template: {TEMPLATE_PATH}")
    print(f"Data:     {OUTPUT_PATH}")
    print()

    webbrowser.open(f'http://localhost:{PORT}')
    app.run(host='127.0.0.1', port=PORT, debug=False)
