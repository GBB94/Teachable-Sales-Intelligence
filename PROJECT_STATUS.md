# PROJECT STATUS

**Last updated:** 2026-02-13
**Updated by:** Opus 4.6 (session 8)

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
- [x] HTML dashboard with 6 tabs (By Feature, By Call, Personas, Competitors, Product Report, Marketing Report)
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
- [x] Google Sheets OAuth2 Desktop auth (replaces service account; token cached at `credentials/token.json`)
- [x] Google Sheets setup, first sync complete (35 prospect-only mentions synced)
- [x] `analyze_features.py cleanup` command — fixes company fields, removes internal speaker mentions
- [x] Internal speaker filter: Zach McCall and all Teachable employees excluded from feature data entirely
- [x] Company inference: empty company fields auto-filled from call title, marketing_data, or attendees
- [x] Personas tab: full filter bar (segment dropdown, company dropdown, date range, collapse/expand)
- [x] Personas tab: 3-column masonry layout for prospect cards (CSS columns, responsive breakpoints)
- [x] Segment dropdown shows all 9 segments with call counts; empty segments shown dimmed with (0)
- [x] Netlify deployment: `test_output/` as publish dir, `netlify.toml` + `_headers` with Basic-Auth
- [x] Dashboard renamed from `dashboard.html` to `index.html` across all references
- [x] Password gate on Scan and Generate Report buttons (JS prompt, prevents accidental pipeline triggers)
- [x] Local Flask server + Netlify both serve `test_output/index.html` — inject once, both stay in sync
- [x] Multi-owner scan: Jerome Olaloye and Kevin Codde added as scan owners (`SCAN_OWNERS` list in `server.py`)
- [x] Owner filter checks both `organizer_email` and `meeting_attendees` (fixes missing calls where prospect organized the invite)
- [x] Jerome and Kevin added to internal speakers lists across all 3 files (analyze_features.py, dashboard_template.html, client.py)
- [x] "Run Analysis" button on By Call tab — password-protected modal showing CLI command with copy-to-clipboard
- [x] 4 new calls analyzed: Red Rover, Transcend Analytics, Biblical Counseling Org, New York Epoxy (41 new features)
- [x] Three-layer categorization safeguards:
  - Layer 1 — Extract prompt prints exact numbered canonical names from categories.json/segments.json with CRITICAL RULES
  - Layer 2 — `analyze_features.py validate` subcommand with fuzzy matching (`--fix` for auto-correction)
  - Layer 3 — NEEDS_REVIEW escalation: inject accepts it, dashboard shows yellow flags, suggested_new_categories tracked in categories.json
- [x] Segment mix bar on Personas tab (horizontal stacked bar showing % of calls per segment, animated, with legend)
- [x] Competitors tab (6th dashboard tab) with mix bar, frequency chart, type filter chips, detail cards
- [x] `competitors.json` — 16 canonical competitors across 5 types (direct, LMS, DIY, adjacent, marketplace)
- [x] Competitor extraction in analysis prompt — mention_types: currently_using, switching_from, evaluated, asked_about, compared_to
- [x] Competitor validation in inject + validate subcommand (fuzzy matching, NEEDS_REVIEW escalation)
- [x] Dashboard reads from both new `competitor_mentions` format and legacy `marketing_data.competitors_mentioned`
- [x] Competitors catalog embedded in DATA for dashboard metadata (type, description per competitor)
- [x] `_load_valid_names()` returns 3 sets: categories, segments, competitors
- [x] Competitor mix bar (horizontal stacked %, animated, same pattern as segment mix bar)
- [x] Competitor exclusion system: dismiss button on cards, password-protected, persists to `excluded_competitors` in features.json
- [x] "Excluded (N)" restore section at bottom of Competitors tab
- [x] `/api/exclude-competitor` server endpoint (exclude/restore actions)
- [x] Competitors tab filter bar: competitor dropdown (multi-select), type dropdown, segment dropdown, date range
- [x] Competitors tab: collapsible detail cards (top 3 expanded, rest collapsed, click header to toggle)
- [x] Competitors tab: Expand All / Collapse All toggle button
- [x] Competitors tab: intel section on canonical cards (differentiator from `competitors.json`, dark background)
- [x] Competitors tab: "Not in competitor database" message for unrecognized competitors
- [x] Competitors tab: inline dismiss button (right-aligned in header row, replaces old hover-only dismiss)
- [x] Segment and date filters now apply to Competitors tab (filter underlying calls)
- [x] Competitors tab: overview cards (seg-card style) with mention count, company count, differentiator bullets
- [x] Competitors tab: uses seg-mix-bar/seg-mix-seg classes (identical to Personas mix bar)
- [x] Competitors tab: frequency bar chart removed (redundant with overview cards + mix bar)
- [x] Competitors tab: `#competitors-view` flex column with 1.5rem gap (matches Personas spacing)
- [x] Competitors tab: Expand/Collapse button inline with "Competitor Details" header (matches Personas pattern)
- [x] Fuzzy competitor name matching: "Learn Worlds" → LearnWorlds, "School (Skool)" → Skool
- [x] Redesigned intel block in detail cards: description (muted) + differentiator (bright) + type badge pill

## What's In Progress

Nothing currently in progress.

---

## Known Issues

- **Empty transcripts.** Some calls have blank `transcript_text`. Use `python3 analyze_features.py refetch-empty test_output/index.html` to re-pull from Fireflies.
- **Exposed API key.** Fireflies key was shown in a chat session. Needs rotation.
- **Confidence scores not yet populated.** The `confidence` field exists in the schema but current analyzed data doesn't have values. Next re-analysis with the updated prompt will populate them.
- **Python 3.9 deprecation warnings.** Google auth libraries warn about Python 3.9 EOL. Functional but should upgrade Python eventually.

### Fixed Issues (session 4)
- **Company fields empty on all 44 mentions** — `cleanup` command infers company from call title, marketing_data, or attendees. All mentions now have companies.
- **Internal speaker mentions in data** — 9 mentions from Zach/Lennie/Sarah/Jonathan removed. Analysis prompt updated to never extract from internal speakers.
- **"Teachable" as company in sheet sync** — `build_row()` `<>` fallback now picks non-Teachable side. Also fixed in cleanup.
- **Sheet setup crash on existing tabs** — `setup_spreadsheet()` rename-before-create fix prevents "Feature Requests already exists" error.

### Fixed Issues (session 3)
- **Teachable/Unknown as company pills** — filtered via `isTeachableInternal()` in dashboard display.
- **Speaker mappings required parentheticals** — fixed in `reportGetCompany()`.

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
  analyze_features.py        # CLI: extract/normalize/inject/validate workflow
  sync_to_sheets.py          # Google Sheets sync (setup, upsert, dry-run)
  generate_reports.py        # Friday cron: flag reports + sync sheets
  retrieve_calls.py          # CLI: pull calls from Fireflies
  models.py                  # Data models, HubSpot note template
  client.py                  # Fireflies API client
  exports.py                 # JSON/CSV/dashboard export functions
  dashboard_template.html    # HTML template with 5 tabs
  categories.json            # 10 feature category definitions
  segments.json              # 9 prospect segment definitions
  competitors.json           # 16 canonical competitors (5 types)
  CLAUDE.md                  # Auto-loaded instructions for Claude Code
  README.md                  # Setup instructions, workflow docs
  PROJECT_STATUS.md          # This file — read first, update at session end
  requirements.txt           # Python dependencies
  .env                       # API keys (not committed)
  .gitignore                 # Excludes credentials/, *.json (except categories.json), test_output/
  .feature_blacklist         # Excluded feature keywords
  .feature_names             # Canonical feature name cache
  credentials/               # OAuth credentials + cached token (gitignored)
  test_output/
    index.html               # Generated dashboard with embedded data (served by Flask + Netlify)
    features.json            # Canonical data artifact
    notes.txt                # Generated HubSpot notes
```

---

## Current Data

- **15 calls** in dashboard (12 analyzed, 2 pending/empty, 1 unanalyzed)
- **76 feature mentions** across **38 unique features** and **12 analyzed calls**
- **10 categories**, zero in "Other" — validated at inject time
- **9 segments** defined in `segments.json` — validated at inject time
- **All 12 analyzed calls have segments assigned**
- **marketing_data** populated on 12 external analyzed calls
- Companies: Speravita, ESI, Simon & Sabine, LTA Singapore, Dot Compliance, Simon Davey, BADM, Red Rover, Transcend Analytics, Biblical Counseling Org, New York Epoxy
- Default scan filters: `owners=[zach.mccall, jerome.olaloye, kevin.codde]`, `keywords=followup/follow-up/follow up/teachable`, `days=14`, `limit=10`

---

## How to Run

```bash
pip3 install -r requirements.txt

# Start server
python3 server.py  # Opens localhost:8080

# Analyze calls
python3 analyze_features.py extract test_output/index.html
# ... Claude analyzes transcripts, outputs features JSON ...
python3 analyze_features.py validate features_output.json        # Check categories/segments
python3 analyze_features.py validate features_output.json --fix  # Auto-correct mismatches
python3 analyze_features.py inject test_output/index.html features_output.json

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
