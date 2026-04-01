#!/usr/bin/env python3
"""
Rebuild test_output/index.html by re-injecting DATA and PERF into the template.

Usage:
    python3 rebuild_dashboard.py

Reads DATA and SEGMENT_DEFS from existing test_output/index.html, reads PERF
from test_output/performance.json, renders dashboard_template.html with all
three, and writes back to test_output/index.html.
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_DIR)

TEMPLATE_PATH = os.path.join(REPO_DIR, "dashboard_template.html")
OUTPUT_DIR = os.path.join(REPO_DIR, "test_output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "index.html")
PERF_PATH = os.path.join(OUTPUT_DIR, "performance.json")


def _extract_json_at(html: str, marker: str) -> dict | None:
    """Extract JSON value assigned to a JS variable after `marker`."""
    idx = html.find(marker)
    if idx == -1:
        return None
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(html, idx + len(marker))
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def extract_data_from_html(html: str) -> dict | None:
    """Extract the embedded DATA JSON from existing index.html."""
    return _extract_json_at(html, "let DATA = ")


def extract_segment_defs_from_html(html: str) -> dict | None:
    """Extract the embedded SEGMENT_DEFS JSON from existing index.html."""
    return _extract_json_at(html, "const SEGMENT_DEFS = ")


def load_performance() -> dict | None:
    if not os.path.exists(PERF_PATH):
        print(f"  WARNING: {PERF_PATH} not found. PERF will be null in output.")
        return None
    with open(PERF_PATH) as f:
        return json.load(f)


def render_dashboard(data: dict, perf: dict | None, segment_defs: dict | None) -> str:
    """Inject DATA, PERF, and SEGMENT_DEFS into dashboard_template.html."""
    # Bake Clay prospecting snapshot if available
    try:
        from lib.clay import get_snapshot, get_seed_companies
        snap = get_snapshot()
        if snap and "error" not in snap:
            data["prospecting_snapshot"] = snap
        seeds = get_seed_companies()
        if seeds and "error" not in seeds:
            data["prospecting_seeds"] = seeds
    except Exception:
        pass

    with open(TEMPLATE_PATH) as f:
        html = f.read()

    html = html.replace("{{DATA_JSON}}", json.dumps(data))
    html = html.replace("{{PERFORMANCE_JSON}}", json.dumps(perf) if perf else "null")
    html = html.replace("{{SEGMENT_DEFS_JSON}}", json.dumps(segment_defs) if segment_defs else "{}")
    return html


def main():
    print("Rebuilding dashboard...")

    if not os.path.exists(OUTPUT_PATH):
        print(f"ERROR: {OUTPUT_PATH} not found. Run server.py first to create initial dashboard.")
        sys.exit(1)

    with open(OUTPUT_PATH) as f:
        existing_html = f.read()

    data = extract_data_from_html(existing_html)
    if not data:
        print("ERROR: Could not extract DATA from existing index.html.")
        sys.exit(1)

    segment_defs = extract_segment_defs_from_html(existing_html)
    if not segment_defs:
        print("  WARNING: Could not extract SEGMENT_DEFS. Using empty object.")
        segment_defs = {}

    perf = load_performance()
    html = render_dashboard(data, perf, segment_defs)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)

    if perf:
        note = f"schema_version={perf.get('schema_version', '?')} generated_at={perf.get('generated_at_et', '?')}"
    else:
        note = "no performance data"
    print(f"  Done. PERF: {note}")
    print(f"  Wrote: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
