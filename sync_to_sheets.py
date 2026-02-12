#!/usr/bin/env python3
"""
Sync feature request data from features.json to Google Sheets.

Usage:
    python3 sync_to_sheets.py                    # Sync latest data
    python3 sync_to_sheets.py --setup            # First-time sheet setup
    python3 sync_to_sheets.py --force            # Re-sync all data (updates all rows)
    python3 sync_to_sheets.py --dry-run          # Show what would be synced
    python3 sync_to_sheets.py --output-dir DIR   # Custom output directory
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_sheets_service():
    """Authenticate with Google Sheets API using service account credentials."""
    creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_path or not os.path.exists(creds_path):
        print("ERROR: Google Sheets credentials not found.")
        print("Set GOOGLE_SHEETS_CREDENTIALS in .env to the path of your service account JSON.")
        print("See README for setup instructions.")
        sys.exit(1)

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        creds_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)


def get_spreadsheet_id():
    """Get the spreadsheet ID from environment."""
    sid = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not sid:
        print("ERROR: GOOGLE_SHEETS_SPREADSHEET_ID not set in .env")
        sys.exit(1)
    return sid


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

def api_call_with_retry(func, max_retries=3, base_delay=2):
    """Execute an API call with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status in (429, 500, 503):
                delay = base_delay * (2 ** attempt)
                print(f"  API error {status}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise
    raise Exception(f"API call failed after {max_retries} retries")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data(output_dir="test_output"):
    """Load feature data from the canonical features.json file."""
    path = os.path.join(output_dir, "features.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No features.json found at {path}. "
            f"Run analyze_features.py inject first."
        )
    with open(path, "r") as f:
        return json.load(f)


def generate_mention_id(call_id, feature_name):
    """Generate a stable, deterministic ID for a mention."""
    raw = f"{call_id}|{feature_name}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def build_row(mention, mention_id, run_id):
    """Convert a mention dict to a sheet row (columns A-R)."""
    # Company: prefer explicit field, fallback to extracting from call_title
    company = mention.get("company", "")
    if not company:
        title = mention.get("call_title", "")
        if "<>" in title:
            parts = title.split("<>")
            company = parts[1].strip().split(":")[0].strip() if len(parts) > 1 else ""

    # Build recording hyperlink formula
    recording_link = ""
    link_url = mention.get("link", "")
    ts_display = mention.get("ts", "")
    if link_url and ts_display:
        recording_link = f'=HYPERLINK("{link_url}", "\u25b6 {ts_display}")'
    elif link_url:
        recording_link = f'=HYPERLINK("{link_url}", "\u25b6 Link")'

    # Calculate week number
    call_date = mention.get("call_date", "")
    week = ""
    if call_date:
        try:
            dt = datetime.strptime(call_date, "%Y-%m-%d")
            week = f"{dt.year}-W{dt.isocalendar()[1]:02d}"
        except ValueError:
            pass

    # Map confidence to human label
    confidence = mention.get("confidence")
    confidence_label = ""
    if isinstance(confidence, (int, float)):
        if confidence >= 0.85:
            confidence_label = "High"
        elif confidence >= 0.65:
            confidence_label = "Medium"
        else:
            confidence_label = "Low - verify"

    return [
        mention_id,                                        # A: Mention ID (hidden)
        mention.get("keyword", ""),                        # B: Feature
        mention.get("category", "Uncategorized"),          # C: Category
        company,                                           # D: Company
        mention.get("speaker", ""),                        # E: Contact
        mention.get("contact_title", ""),                  # F: Contact Title
        mention.get("type", "prospect_request"),           # G: Request Type
        mention.get("text", ""),                           # H: Quote
        ts_display,                                        # I: Timestamp
        recording_link,                                    # J: Recording Link
        mention.get("call_title", ""),                     # K: Call Title
        call_date,                                         # L: Call Date
        week,                                              # M: Week
        confidence_label,                                  # N: Confidence
        "",                                                # O: Status (product)
        "",                                                # P: Priority (product)
        "",                                                # Q: Jira Link (product)
        "",                                                # R: Notes (product)
    ]


# ---------------------------------------------------------------------------
# Sheet operations
# ---------------------------------------------------------------------------

def get_existing_mention_ids(service, spreadsheet_id):
    """Read all existing Mention IDs from column A of Feature Requests tab."""
    result = api_call_with_retry(lambda: service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="'Feature Requests'!A3:A",
    ).execute())
    values = result.get("values", [])
    return {row[0]: i + 3 for i, row in enumerate(values) if row}


def append_rows(service, spreadsheet_id, rows):
    """Append multiple rows in a single API call."""
    api_call_with_retry(lambda: service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="'Feature Requests'!A3",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute())


def update_rows(service, spreadsheet_id, updates):
    """Update multiple rows in a single batch call. Only touches columns A-N."""
    batch_data = []
    for update in updates:
        row_num = update["row_number"]
        batch_data.append({
            "range": f"'Feature Requests'!A{row_num}:N{row_num}",
            "values": [update["data"]],
        })

    # Batch in chunks of 200
    for i in range(0, len(batch_data), 200):
        chunk = batch_data[i : i + 200]
        api_call_with_retry(lambda c=chunk: service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": c},
        ).execute())


def update_last_synced(service, spreadsheet_id):
    """Update the 'Last Synced' timestamp in row 1."""
    now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    api_call_with_retry(lambda: service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="'Feature Requests'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [[f"Last Synced: {now}"]]},
    ).execute())


def log_sync_run(service, spreadsheet_id, run_id, rows_added, rows_updated, errors=None):
    """Append a row to the Sync Log tab."""
    git_hash = ""
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        git_hash = "unknown"

    log_row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        run_id,
        rows_added,
        rows_updated,
        errors or "",
        git_hash,
    ]
    api_call_with_retry(lambda: service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="'Sync Log'!A2",
        valueInputOption="USER_ENTERED",
        body={"values": [log_row]},
    ).execute())


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync(output_dir="test_output", force=False, dry_run=False):
    """Main sync function with upsert semantics."""
    service = get_sheets_service()
    spreadsheet_id = get_spreadsheet_id()
    run_id = str(uuid.uuid4())[:8]

    # 1. Load canonical data
    data = load_data(output_dir)

    # 2. Get existing mention IDs from sheet
    existing = {}
    if not force:
        try:
            existing = get_existing_mention_ids(service, spreadsheet_id)
        except Exception as e:
            print(f"  Could not read existing rows (sheet may be empty): {e}")

    # 3. Sort mentions into inserts and updates
    to_insert = []
    to_update = []

    for mention in data.get("mentions", []):
        mid = mention.get("mention_id") or generate_mention_id(
            mention.get("call_id", ""),
            mention.get("keyword", ""),
        )

        row_data = build_row(mention, mid, run_id)

        if mid in existing:
            to_update.append({
                "row_number": existing[mid],
                "data": row_data[:14],  # Only sync-owned columns A-N
            })
        else:
            to_insert.append(row_data)

    # 4. Report
    print(f"Sync run {run_id}:")
    print(f"  {len(data.get('mentions', []))} total mentions in features.json")
    print(f"  {len(existing)} existing rows in sheet")
    print(f"  {len(to_insert)} new rows to insert")
    print(f"  {len(to_update)} existing rows to update")

    if dry_run:
        print("\n  DRY RUN — no changes written.\n")
        if to_insert:
            print("  New rows that would be inserted:")
            for row in to_insert:
                print(f"    {row[1]} | {row[2]} | {row[3]} | {row[6]}")
        if to_update:
            print("  Rows that would be updated:")
            for u in to_update:
                print(f"    Row {u['row_number']}: {u['data'][1]} | {u['data'][2]} | {u['data'][3]}")
        return {"rows_added": len(to_insert), "rows_updated": len(to_update)}

    # 5. Execute inserts
    if to_insert:
        append_rows(service, spreadsheet_id, to_insert)
        print(f"  Inserted {len(to_insert)} rows")

    # 6. Execute updates (sync-owned columns only)
    if to_update:
        update_rows(service, spreadsheet_id, to_update)
        print(f"  Updated {len(to_update)} rows")

    # 7. Update "Last Synced" timestamp
    update_last_synced(service, spreadsheet_id)

    # 8. Log to Sync Log tab
    try:
        log_sync_run(service, spreadsheet_id, run_id, len(to_insert), len(to_update))
    except Exception as e:
        print(f"  Warning: could not write sync log: {e}")

    print("  Sync complete.")
    return {"rows_added": len(to_insert), "rows_updated": len(to_update)}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_spreadsheet(service, spreadsheet_id):
    """Create all tabs, headers, formatting, formulas, validation, and protection."""

    # Get existing sheet info
    meta = api_call_with_retry(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    existing_sheets = {s["properties"]["title"]: s["properties"]["sheetId"]
                       for s in meta.get("sheets", [])}

    tabs_needed = [
        "Feature Requests",
        "Feature Summary",
        "Category Rollup",
        "Company View",
        "Sync Log",
    ]

    requests = []

    # Create missing tabs
    for tab in tabs_needed:
        if tab not in existing_sheets:
            requests.append({
                "addSheet": {
                    "properties": {"title": tab}
                }
            })

    # Rename default "Sheet1" if it exists and Feature Requests doesn't
    if "Sheet1" in existing_sheets and "Feature Requests" not in existing_sheets:
        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": existing_sheets["Sheet1"],
                    "title": "Feature Requests",
                },
                "fields": "title",
            }
        })

    if requests:
        api_call_with_retry(lambda: service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute())
        print(f"  Created/renamed tabs")

    # Re-fetch sheet IDs after creation
    meta = api_call_with_retry(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"]
                 for s in meta.get("sheets", [])}

    # ---- Write headers ----
    headers = {
        "'Feature Requests'!A1": [["Last Synced: (not yet synced)"]],
        "'Feature Requests'!A2:R2": [[
            "Mention ID", "Feature", "Category", "Company", "Contact",
            "Contact Title", "Request Type", "Quote", "Timestamp",
            "Recording", "Call Title", "Call Date", "Week", "Confidence",
            "Status", "Priority", "Jira Link", "Notes",
        ]],
        "'Feature Summary'!A1:I1": [[
            "Feature", "Category", "Total Mentions", "Unique Companies",
            "Companies", "First Seen", "Last Seen", "Top Quote", "Status",
        ]],
        "'Category Rollup'!A1:F1": [[
            "Category", "Total Features", "Total Mentions",
            "Unique Companies", "% of All Requests", "Top Feature",
        ]],
        "'Company View'!A1:F1": [[
            "Company", "Total Calls", "Features Requested",
            "Top Features", "Categories", "Last Call",
        ]],
        "'Sync Log'!A1:F1": [[
            "Timestamp", "Run ID", "Rows Added", "Rows Updated",
            "Errors", "Git Commit",
        ]],
    }

    batch_data = []
    for range_str, values in headers.items():
        batch_data.append({"range": range_str, "values": values})

    api_call_with_retry(lambda: service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": batch_data},
    ).execute())
    print("  Wrote headers")

    # ---- Formatting ----
    fr_id = sheet_ids.get("Feature Requests", 0)
    fs_id = sheet_ids.get("Feature Summary", 0)
    cr_id = sheet_ids.get("Category Rollup", 0)
    cv_id = sheet_ids.get("Company View", 0)
    sl_id = sheet_ids.get("Sync Log", 0)

    fmt_requests = []

    # Column widths for Feature Requests
    col_widths = {
        0: 100, 1: 220, 2: 200, 3: 160, 4: 140, 5: 140,
        6: 130, 7: 400, 8: 80, 9: 100, 10: 200,
        11: 100, 12: 80, 13: 100, 14: 120, 15: 80,
        16: 140, 17: 300,
    }
    for col_idx, width in col_widths.items():
        fmt_requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": fr_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    # Freeze rows/cols
    for sid, rows, cols in [
        (fr_id, 2, 2), (fs_id, 1, 1), (cr_id, 1, 1), (cv_id, 1, 1), (sl_id, 1, 0)
    ]:
        fmt_requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sid,
                    "gridProperties": {
                        "frozenRowCount": rows,
                        "frozenColumnCount": cols,
                    },
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        })

    # Bold headers on Feature Requests (row 2)
    fmt_requests.append({
        "repeatCell": {
            "range": {"sheetId": fr_id, "startRowIndex": 1, "endRowIndex": 2,
                       "startColumnIndex": 0, "endColumnIndex": 18},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True, "fontSize": 10},
                    "backgroundColor": {"red": 0.91, "green": 0.93, "blue": 0.95},
                }
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    })

    # Bold headers on summary tabs (row 1)
    for sid in [fs_id, cr_id, cv_id, sl_id]:
        fmt_requests.append({
            "repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                           "startColumnIndex": 0, "endColumnIndex": 10},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 10},
                        "backgroundColor": {"red": 0.91, "green": 0.93, "blue": 0.95},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        })

    # Hide Mention ID column (column A)
    fmt_requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": fr_id,
                "dimension": "COLUMNS",
                "startIndex": 0,
                "endIndex": 1,
            },
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }
    })

    # Text wrap on Quote column (H = index 7)
    fmt_requests.append({
        "repeatCell": {
            "range": {"sheetId": fr_id, "startRowIndex": 2,
                       "startColumnIndex": 7, "endColumnIndex": 8},
            "cell": {
                "userEnteredFormat": {"wrapStrategy": "WRAP"}
            },
            "fields": "userEnteredFormat.wrapStrategy",
        }
    })

    # Alternating row colors
    fmt_requests.append({
        "addBanding": {
            "bandedRange": {
                "range": {"sheetId": fr_id, "startRowIndex": 1,
                           "startColumnIndex": 0, "endColumnIndex": 18},
                "rowProperties": {
                    "headerColor": {"red": 0.91, "green": 0.93, "blue": 0.95},
                    "firstBandColor": {"red": 1, "green": 1, "blue": 1},
                    "secondBandColor": {"red": 0.97, "green": 0.98, "blue": 0.98},
                },
            }
        }
    })

    # ---- Conditional formatting rules ----
    cond_rules = [
        # Status column (O = index 14)
        (14, "Shipped", {"red": 0.83, "green": 0.93, "blue": 0.85}),
        (14, "Planned", {"red": 0.80, "green": 0.90, "blue": 1.0}),
        (14, "In Progress", {"red": 1.0, "green": 0.95, "blue": 0.80}),
        (14, "Under Review", {"red": 0.89, "green": 0.89, "blue": 0.90}),
        (14, "Won't Do", {"red": 0.97, "green": 0.84, "blue": 0.86}),
        # Confidence column (N = index 13)
        (13, "Low - verify", {"red": 1.0, "green": 0.95, "blue": 0.80}),
        (13, "Medium", {"red": 0.89, "green": 0.89, "blue": 0.90}),
        (13, "High", {"red": 0.83, "green": 0.93, "blue": 0.85}),
        # Priority column (P = index 15)
        (15, "P0", {"red": 0.97, "green": 0.84, "blue": 0.86}),
        (15, "P1", {"red": 1.0, "green": 0.95, "blue": 0.80}),
        (15, "P2", {"red": 0.80, "green": 0.90, "blue": 1.0}),
        (15, "P3", {"red": 0.89, "green": 0.89, "blue": 0.90}),
    ]

    for col_idx, value, bg in cond_rules:
        fmt_requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": fr_id,
                        "startRowIndex": 2,
                        "startColumnIndex": col_idx,
                        "endColumnIndex": col_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": value}],
                        },
                        "format": {"backgroundColor": bg},
                    },
                },
                "index": 0,
            }
        })

    # ---- Data validation dropdowns ----
    validations = [
        # Status (O = index 14)
        (14, ["Under Review", "Planned", "In Progress", "Shipped", "Won't Do"]),
        # Priority (P = index 15)
        (15, ["P0", "P1", "P2", "P3"]),
        # Request Type (G = index 6)
        (6, ["prospect_request", "prospect_interest", "rep_highlighted"]),
    ]

    for col_idx, values in validations:
        fmt_requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": fr_id,
                    "startRowIndex": 2,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in values],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        })

    # ---- Column protection (A-N, sync-owned) ----
    fmt_requests.append({
        "addProtectedRange": {
            "protectedRange": {
                "range": {
                    "sheetId": fr_id,
                    "startColumnIndex": 0,
                    "endColumnIndex": 14,
                    "startRowIndex": 2,
                },
                "description": "Auto-synced data. Only the sync service can edit.",
                "warningOnly": False,
                "editors": {
                    "users": ["zmccall94@gmail.com"],
                },
            }
        }
    })

    # Execute all formatting in one batch
    api_call_with_retry(lambda: service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": fmt_requests},
    ).execute())
    print("  Applied formatting, validation, conditional formatting, and protection")

    # ---- Add note on Confidence header ----
    confidence_note = (
        "Confidence indicates how certain the AI categorization is:\n"
        "- High (0.85+): Category assignment is very likely correct\n"
        "- Medium (0.65-0.84): Probably correct but worth a glance\n"
        "- Low - verify (<0.65): Category may be wrong. Check the quote."
    )
    note_request = {
        "updateCells": {
            "range": {
                "sheetId": fr_id,
                "startRowIndex": 1,
                "endRowIndex": 2,
                "startColumnIndex": 13,
                "endColumnIndex": 14,
            },
            "rows": [{"values": [{"note": confidence_note}]}],
            "fields": "note",
        }
    }
    api_call_with_retry(lambda: service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [note_request]},
    ).execute())
    print("  Added confidence header note")

    # ---- Write formulas on summary tabs ----
    summary_formulas = {
        "'Feature Summary'!A2": [['=SORT(UNIQUE(\'Feature Requests\'!B3:B), 1, TRUE)']],
        "'Feature Summary'!B2": [['=IFERROR(INDEX(\'Feature Requests\'!C:C, MATCH(A2, \'Feature Requests\'!B:B, 0)), "")']],
        "'Feature Summary'!C2": [['=COUNTIF(\'Feature Requests\'!B:B, A2)']],
        "'Feature Summary'!D2": [['=IFERROR(COUNTA(UNIQUE(FILTER(\'Feature Requests\'!D3:D, \'Feature Requests\'!B3:B=A2))), 0)']],
        "'Feature Summary'!E2": [['=IFERROR(TEXTJOIN(", ", TRUE, UNIQUE(FILTER(\'Feature Requests\'!D3:D, \'Feature Requests\'!B3:B=A2))), "")']],
        "'Feature Summary'!F2": [['=IFERROR(MINIFS(\'Feature Requests\'!L3:L, \'Feature Requests\'!B3:B, A2), "")']],
        "'Feature Summary'!G2": [['=IFERROR(MAXIFS(\'Feature Requests\'!L3:L, \'Feature Requests\'!B3:B, A2), "")']],
        "'Feature Summary'!H2": [['=IFERROR(INDEX(\'Feature Requests\'!H:H, MATCH(A2, \'Feature Requests\'!B:B, 0)), "")']],

        "'Category Rollup'!A2": [['=SORT(UNIQUE(\'Feature Requests\'!C3:C), 1, TRUE)']],
        "'Category Rollup'!B2": [['=IFERROR(COUNTA(UNIQUE(FILTER(\'Feature Requests\'!B3:B, \'Feature Requests\'!C3:C=A2))), 0)']],
        "'Category Rollup'!C2": [['=COUNTIF(\'Feature Requests\'!C:C, A2)']],
        "'Category Rollup'!D2": [['=IFERROR(COUNTA(UNIQUE(FILTER(\'Feature Requests\'!D3:D, \'Feature Requests\'!C3:C=A2))), 0)']],
        "'Category Rollup'!E2": [['=IFERROR(C2/COUNTA(\'Feature Requests\'!C3:C), 0)']],
        "'Category Rollup'!F2": [['=IFERROR(INDEX(\'Feature Requests\'!B:B, MATCH(A2, \'Feature Requests\'!C:C, 0)), "")']],

        "'Company View'!A2": [['=SORT(UNIQUE(\'Feature Requests\'!D3:D), 1, TRUE)']],
        "'Company View'!B2": [['=IFERROR(COUNTA(UNIQUE(FILTER(\'Feature Requests\'!K3:K, \'Feature Requests\'!D3:D=A2))), 0)']],
        "'Company View'!C2": [['=COUNTIF(\'Feature Requests\'!D:D, A2)']],
        "'Company View'!D2": [['=IFERROR(TEXTJOIN(", ", TRUE, UNIQUE(FILTER(\'Feature Requests\'!B3:B, \'Feature Requests\'!D3:D=A2))), "")']],
        "'Company View'!E2": [['=IFERROR(TEXTJOIN(", ", TRUE, UNIQUE(FILTER(\'Feature Requests\'!C3:C, \'Feature Requests\'!D3:D=A2))), "")']],
        "'Company View'!F2": [['=IFERROR(MAXIFS(\'Feature Requests\'!L3:L, \'Feature Requests\'!D3:D, A2), "")']],
    }

    formula_data = [{"range": r, "values": v} for r, v in summary_formulas.items()]
    api_call_with_retry(lambda: service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": formula_data},
    ).execute())
    print("  Wrote summary tab formulas")

    # Format % column on Category Rollup
    pct_request = {
        "repeatCell": {
            "range": {"sheetId": cr_id, "startRowIndex": 1,
                       "startColumnIndex": 4, "endColumnIndex": 5},
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "PERCENT", "pattern": "0%"}
                }
            },
            "fields": "userEnteredFormat.numberFormat",
        }
    }
    api_call_with_retry(lambda: service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [pct_request]},
    ).execute())

    print("\n  Setup complete! Share the sheet with your product team.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sync feature request data to Google Sheets"
    )
    parser.add_argument("--setup", action="store_true",
                        help="First-time sheet setup (create tabs, headers, formatting)")
    parser.add_argument("--force", action="store_true",
                        help="Update all rows regardless of changes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be synced without writing")
    parser.add_argument("--output-dir", default="test_output",
                        help="Directory containing features.json (default: test_output)")
    args = parser.parse_args()

    if args.setup:
        print("Setting up spreadsheet...")
        service = get_sheets_service()
        spreadsheet_id = get_spreadsheet_id()
        setup_spreadsheet(service, spreadsheet_id)
        return

    result = sync(
        output_dir=args.output_dir,
        force=args.force,
        dry_run=args.dry_run,
    )
    return result


if __name__ == "__main__":
    main()
