#!/usr/bin/env python3
"""Flag that reports are due for CC analysis.

Reads the dashboard HTML, checks for pending/unanalyzed calls,
writes a .reports_due flag file with timestamp and pending call count.

Cron: 0 15 * * 5 cd /path/to/sales-intelligence && python3 generate_reports.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_PATH = os.path.join(BASE_DIR, "test_output", "dashboard.html")
FLAG_PATH = os.path.join(BASE_DIR, ".reports_due")


def extract_data_from_html(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        html = f.read()
    match = re.search(r"const DATA = ({.*?});\s*\n", html, re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(1))


def main():
    data = extract_data_from_html(DASHBOARD_PATH)
    if data is None:
        print(f"No dashboard found at {DASHBOARD_PATH}")
        sys.exit(1)

    calls = data.get("calls", [])
    pending = [c for c in calls if c.get("pending_analysis", False)]
    total = len(calls)

    now = datetime.now(timezone.utc).isoformat()

    if not pending:
        print(f"No pending calls ({total} total). Reports are up to date.")
        if os.path.exists(FLAG_PATH):
            os.remove(FLAG_PATH)
        return

    # Write flag file
    flag = {
        "timestamp": now,
        "pending_count": len(pending),
        "total_calls": total,
        "pending_call_ids": [c.get("id", "") for c in pending],
    }
    with open(FLAG_PATH, "w") as f:
        json.dump(flag, f, indent=2)

    print(f"Reports due: {len(pending)} pending calls out of {total} total")
    print(f"Flag written to {FLAG_PATH}")
    print(f"Timestamp: {now}")

    # Sync feature data to Google Sheets
    try:
        from sync_to_sheets import sync
        result = sync(output_dir=os.path.dirname(DASHBOARD_PATH))
        print(f"Sheet sync: {result['rows_added']} added, {result['rows_updated']} updated")
    except Exception as e:
        print(f"Sheet sync failed (non-fatal): {e}")


if __name__ == "__main__":
    main()
