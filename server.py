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
import sys
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
SCAN_LIMIT = 10
SCAN_TITLE_KEYWORDS = DEFAULT_SCAN_TITLE_KEYWORDS
SCAN_OWNER = 'zach.mccall'
SCAN_EXCLUDE_DOMAINS = ['teachable.com']
PORT = 8080

TEMPLATE_PATH = os.path.join(REPO_DIR, 'dashboard_template.html')
OUTPUT_DIR = os.path.join(REPO_DIR, 'test_output')
OUTPUT_PATH = os.path.join(OUTPUT_DIR, 'index.html')

app = Flask(__name__)

# In-memory cache of the last preview fetch so /process can use the Call objects
# without re-fetching from Fireflies.
_preview_cache = {}  # call_id -> Call object


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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
    """Read the template and inject DATA JSON."""
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
            owner_emails=[SCAN_OWNER],
        )

        print(f"[preview] Fetching calls (days_back={SCAN_DAYS}, limit={SCAN_LIMIT}, owner={SCAN_OWNER})...")
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
    app.run(host='0.0.0.0', port=PORT, debug=False)
