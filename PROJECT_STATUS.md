# PROJECT STATUS

**Last updated:** 2026-02-12
**Updated by:** Opus 4.6 (session 3)

---

## Quick Summary

Teachable Sales Intelligence. Pulls call transcripts from Fireflies, uses Claude to extract features and insights, generates dashboards and reports for product and marketing teams. Feature data syncs to Google Sheets for product team tracking.

**Repo:** https://github.com/GBB94/Teachable-Sales-Intelligence (private)
**Local:** `/Users/zachmccall/call-puller/`

---

## What's Working

- [x] Fireflies API integration (pull calls, filter by owner/keywords/date)
- [x] Interactive call approval (select which calls to process)
- [x] AI feature extraction (Claude reads transcripts, identifies features)
- [x] Feature normalization (merge duplicate feature names)
- [x] HTML dashboard with 5 tabs (By Feature, By Call, Personas, Product Report, Marketing Report)
- [x] HubSpot notes generation (structured sales qualification template)
- [x] Local Flask server with scan/approve workflow
- [x] Exclude functionality (gray out calls, persist via URL hash)
- [x] Client-side filters (date range, company dropdown, segment dropdown, search)
- [x] AI-driven feature categorization (10 categories in `categories.json`, assigned at analysis time, zero "Other")
- [x] Canonical `features.json` data file (written by `analyze_features.py inject`)
- [x] Stable mention IDs (`sha1(call_id|feature_name)[:12]` for upsert)
- [x] `sync_to_sheets.py` — Google Sheets sync with upsert, setup, formatting, protection, audit log
- [x] Dashboard "Sync to Sheets" button + `/api/sync-sheets` server endpoint
- [x] `--sync-sheets` flag on `analyze_features.py inject`
- [x] Friday cron sync in `generate_reports.py`
- [x] Product Report: flat 4-section layout (Summary, Calls by Segment, Feature Recap, Customers This Week, Feature Themes by category)
- [x] Marketing Report: 4-section structure (Who We Talked To, What They're Saying, Objections & Competitive, Buying Signals & Timeline)
- [x] Per-call `marketing_data` extraction prompt (quotes, terminology, objections, competitors, buying signals, timeline)
- [x] Scan/report separation (scan is fast, reports only on demand or cron)
- [x] README with setup instructions, confidence docs, workflow reference
- [x] `CLAUDE.md` for automatic session context loading
- [x] `PROJECT_STATUS.md` for cross-session continuity
- [x] Company pills on By Feature tab (one pill per company, hover shows contacts, capped at 3 + overflow)
- [x] Internal call filter (calls with "sales" in title excluded from Marketing Report + analysis)
- [x] Title keyword filter fix (CLI now defaults to same keywords as server: teachable/followup)
- [x] Persona/segment categorization infrastructure (`segments.json`, analysis prompt, inject pipeline, dashboard display)
- [x] 9 segments defined (CE & Credentialing, Professional Training, Coaches, Associations, Course Creators, Academic, Corporate Education, Health & Wellness, Government & Public Sector Education)
- [x] Segment dropdown filter (By Feature, By Call, and Personas tabs)
- [x] Segment pill on By Call card headers
- [x] Product Report: Calls by Segment section
- [x] Marketing Report: Segment shown on company cards in Who We Talked To
- [x] Personas tab: Segment Overview cards, Segment Comparison table, Prospect Cards by Segment
- [x] Company attribution bug fixes (Teachable/Unknown no longer appear as company pills)
- [x] Analysis prompt requires `company` field on every feature, never "Teachable" or "Unknown"
- [x] Speaker-to-company mapping works without parentheticals (Simon Smith, Sabine Lehner, etc.)
- [x] Project renamed from "call-puller" to "Teachable Sales Intelligence" in all code references

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
- **Exposed API key.** Fireflies key was shown in a chat session. Needs rotation.
- **Confidence scores not yet populated.** The `confidence` field exists in the schema but current analyzed data doesn't have values. Next re-analysis with the updated prompt will populate them.
- **Company field empty on existing mentions.** All 44 existing mentions have `company: ""`. The updated analysis prompt now requires it. Next re-analysis will populate. Dashboard fallback logic handles this for now.
- **2 mentions unresolvable.** Two `rep_highlighted` mentions from Teachable speakers on generic-titled calls have no company attribution (Zach on "Teachable Followup", Jonathan on "Teachable Learning Paths/Quizzes"). Next re-analysis will fix via `company` field.
- **Segments populated on all 8 analyzed calls.** Previously only 1 had a segment; full re-analysis assigned segments to all.

### Fixed Issues (this session)
- **Teachable appearing as company pill** — `getCompany()` and `reportGetCompany()` now filter out "Teachable" via `isTeachableInternal()`. Internal speakers fall through to call title / attendees extraction.
- **Unknown appearing as company pill** — same filter rejects "Unknown". `buildFeatureCard()` skips mentions with no resolvable company instead of labeling them "Unknown".
- **Speaker mappings required parentheticals** — `reportGetCompany()` known-speaker lookups (Simon Smith, Sabine, etc.) now work without `(Company)` parenthetical in speaker name.

---

## Architecture

```
Fireflies API
    |
    v
retrieve_calls.py (pull + filter + approve)
    |
    v
analyze_features.py (Claude extracts features + categories + segments)
    |
    v
features.json  <-- CANONICAL DATA FILE
    |
    +---> dashboard_template.html (interactive dashboard, 5 tabs)
    +---> sync_to_sheets.py (Google Sheets sync)
    +---> generate_reports.py (Friday cron)
```

---

## File Map

```
sales-intelligence/
  server.py                  # Flask server (localhost:8080)
  analyze_features.py        # CLI: extract/normalize/inject workflow
  sync_to_sheets.py          # Google Sheets sync (setup, upsert, dry-run)
  generate_reports.py        # Friday cron: flag reports + sync sheets
  retrieve_calls.py          # CLI: pull calls from Fireflies
  models.py                  # Data models, HubSpot note template
  client.py                  # Fireflies API client
  exports.py                 # JSON/CSV/dashboard export functions
  dashboard_template.html    # HTML template with 5 tabs
  categories.json            # 10 feature category definitions
  segments.json              # 9 prospect segment definitions
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

- **10 calls** in dashboard (8 analyzed, 1 pending, 1 empty transcript; 2 internal calls removed)
- **44 feature mentions** across **24 unique features** and **8 analyzed calls**
- **10 categories**, zero in "Other"
- **9 segments** defined in `segments.json` (CE & Credentialing, Professional Training, Coaches, Associations, Course Creators, Academic, Corporate Education, Health & Wellness, Government & Public Sector Education)
- **All 8 analyzed calls have segments assigned**
- **marketing_data** populated on 8 external analyzed calls
- Companies: Speravita, ESI (Anne Blocker), Simon & Sabine, LTA Singapore, Dot Compliance, Simon Davey, BADM
- Default scan filters: `owner=zach.mccall`, `keywords=followup/follow-up/follow up/teachable`, `days=14`, `limit=10`

---

## How to Run

```bash
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
3. Work in the local project directory (NEVER use temp directories)
4. Commit after each meaningful change
5. Update this file at the end of the session with what changed

**When updating this file:**

- Move completed items from "In Progress" to "What's Working"
- Update "Known Issues" (add new ones, remove resolved ones)
- Update "Current Data" if new calls were analyzed
- Change "Last updated" date and "Updated by" field
