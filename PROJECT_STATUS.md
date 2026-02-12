# PROJECT STATUS

**Last updated:** 2026-02-11
**Updated by:** Opus 4.6 (end of session)

---

## Quick Summary

Sales intelligence tool for Teachable. Pulls call transcripts from Fireflies, uses Claude to extract features and insights, generates dashboards and reports for product and marketing teams. Feature data syncs to Google Sheets for product team tracking.

**Repo:** https://github.com/GBB94/call-puller (private)
**Local:** `/Users/zachmccall/call-puller/`

---

## What's Working

- [x] Fireflies API integration (pull calls, filter by owner/keywords/date)
- [x] Interactive call approval (select which calls to process)
- [x] AI feature extraction (Claude reads transcripts, identifies features)
- [x] Feature normalization (merge duplicate feature names)
- [x] HTML dashboard with 4 tabs (By Feature, By Call, Product Report, Marketing Report)
- [x] HubSpot notes generation (structured sales qualification template)
- [x] Local Flask server with scan/approve workflow
- [x] Exclude functionality (gray out calls, persist via URL hash)
- [x] Client-side filters (date range, company dropdown, search)
- [x] AI-driven feature categorization (10 categories in `categories.json`, assigned at analysis time, zero "Other")
- [x] Canonical `features.json` data file (written by `analyze_features.py inject`)
- [x] Stable mention IDs (`sha1(call_id|feature_name)[:12]` for upsert)
- [x] `sync_to_sheets.py` — Google Sheets sync with upsert, setup, formatting, protection, audit log
- [x] Dashboard "Sync to Sheets" button + `/api/sync-sheets` server endpoint
- [x] `--sync-sheets` flag on `analyze_features.py inject`
- [x] Friday cron sync in `generate_reports.py`
- [x] Product Report: flat 4-section layout (Summary, Feature Recap, Customers This Week, Feature Themes by category)
- [x] Marketing Report: persona profiles, voice of customer, pain points, objections, buying signals
- [x] Scan/report separation (scan is fast, reports only on demand or cron)
- [x] README with setup instructions, confidence docs, workflow reference
- [x] `CLAUDE.md` for automatic session context loading
- [x] `PROJECT_STATUS.md` for cross-session continuity

## What's In Progress

### 1. Google Cloud Setup (Manual — Zach)

The `sync_to_sheets.py` script is built and ready. Zach needs to:

1. Create Google Cloud project, enable Sheets API + Drive API
2. Create service account, download JSON credentials to `credentials/sheets_service_account.json`
3. Create Google Sheet "Sales Intelligence - Feature Requests", share with service account
4. Add to `.env`: `GOOGLE_SHEETS_CREDENTIALS` and `GOOGLE_SHEETS_SPREADSHEET_ID`
5. Run: `python3 sync_to_sheets.py --setup` then `--dry-run` then first sync

**Do NOT skip the `--dry-run` test before the first real sync.**

---

## Known Issues

- **Empty transcripts.** Some calls have blank `transcript_text`. Use `python3 analyze_features.py refetch-empty test_output/dashboard.html` to re-pull from Fireflies.
- **Directory confusion.** CC previously worked in `/private/tmp/call-puller-zip/inner/` (temp dir). All work must happen in `/Users/zachmccall/call-puller/`. The temp dir should not be used.
- **Exposed API key.** Fireflies key was shown in a chat session. Needs rotation.
- **Confidence scores not yet populated.** The `confidence` field exists in the schema but current analyzed data doesn't have values. Next re-analysis with the updated prompt will populate them.
- **Company field sparsely populated.** Many mentions have company extracted from call title only. The `company` field in the analysis prompt should be filled by Claude during next re-analysis.

---

## Architecture

```
Fireflies API
    |
    v
retrieve_calls.py (pull + filter + approve)
    |
    v
analyze_features.py (Claude extracts features + categories)
    |
    v
features.json  <-- CANONICAL DATA FILE
    |
    +---> dashboard_template.html (interactive dashboard)
    +---> sync_to_sheets.py (Google Sheets sync)
    +---> generate_reports.py (Friday cron)
```

---

## File Map

```
call-puller/
  server.py                  # Flask server (localhost:8080)
  analyze_features.py        # CLI: extract/normalize/inject workflow
  sync_to_sheets.py          # Google Sheets sync (setup, upsert, dry-run)
  generate_reports.py        # Friday cron: flag reports + sync sheets
  retrieve_calls.py          # CLI: pull calls from Fireflies
  models.py                  # Data models, HubSpot note template
  client.py                  # Fireflies API client
  exports.py                 # JSON/CSV/dashboard export functions
  dashboard_template.html    # HTML template with 4 tabs
  categories.json            # 10 feature category definitions
  CLAUDE.md                  # Auto-loaded instructions for Claude Code
  README.md                  # Setup instructions, workflow docs
  PROJECT_STATUS.md          # This file — read first, update at session end
  requirements.txt           # Python dependencies
  .env                       # API keys (not committed)
  .gitignore                 # Excludes credentials/, *.json (except categories.json), test_output/
  .feature_blacklist         # Excluded feature keywords
  .feature_names             # Canonical feature name cache
  credentials/               # Google service account JSON (gitignored)
  test_output/
    dashboard.html           # Generated dashboard with embedded data
    features.json            # Canonical data artifact
    notes.txt                # Generated HubSpot notes
```

---

## Current Data

- **12 calls** in dashboard
- **38 feature mentions** extracted
- **10 categories**, zero in "Other"
- Companies: Speravita, Ellyn Satter Institute, Simon & Sabine, LTA, Dot Compliance, Simon Davey
- Default scan filters: `owner=zach.mccall`, `keywords=followup/follow-up/follow up/teachable`, `days=14`, `limit=10`

---

## How to Run

```bash
cd /Users/zachmccall/call-puller
pip3 install -r requirements.txt

# Start server
python3 server.py  # Opens localhost:8080

# Analyze calls
python3 analyze_features.py extract test_output/dashboard.html
# ... Claude analyzes transcripts, outputs features JSON ...
python3 analyze_features.py inject test_output/dashboard.html features_output.json

# Sync to Google Sheets
python3 sync_to_sheets.py --dry-run  # Preview
python3 sync_to_sheets.py            # Sync
```

---

## Session Instructions for Claude Code

**When starting a new session on this project:**

1. Read this file first (`PROJECT_STATUS.md`)
2. Check the "In Progress" section for current work
3. Work in `/Users/zachmccall/call-puller/` (NEVER use temp directories)
4. Commit after each meaningful change
5. Update this file at the end of the session with what changed

**When updating this file:**

- Move completed items from "In Progress" to "What's Working"
- Update "Known Issues" (add new ones, remove resolved ones)
- Update "Current Data" if new calls were analyzed
- Change "Last updated" date and "Updated by" field
