# PROJECT STATUS

**Last updated:** 2026-02-16
**Updated by:** Opus 4.6 (session 13)

---

## Quick Summary

Teachable Sales Intelligence. Pulls call transcripts from Fireflies, uses Claude to extract features and insights, generates dashboards and reports for product and marketing teams. Feature data syncs to Google Sheets for product team tracking.

**Repo:** https://github.com/GBB94/Teachable-Sales-Intelligence (private)
**Local:** repo root (the directory containing `server.py` and `dashboard_template.html`)

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
- [x] `competitors.json` — 19 canonical competitors across 5 types (direct, LMS, DIY, adjacent, marketplace)
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
- [x] Competitors tab: overview cards (seg-card style) with mention count, company count, short_description
- [x] Competitors tab: uses seg-mix-bar/seg-mix-seg classes (identical to Personas mix bar)
- [x] Competitors tab: frequency bar chart removed (redundant with overview cards + mix bar)
- [x] Competitors tab: `#competitors-view` flex column with 1.5rem gap (matches Personas spacing)
- [x] Competitors tab: Expand/Collapse button inline with "Competitor Details" header (matches Personas pattern)
- [x] Fuzzy competitor name matching: "Learn Worlds" → LearnWorlds, "School (Skool)" → Skool
- [x] Redesigned intel block in detail cards: description (muted) + differentiator (bright) + type badge pill
- [x] Action password updated to match Netlify Basic-Auth credential across all password-protected actions
- [x] `competitors.json` — `short_description` field added to all 18 competitors (one-liner summaries)
- [x] 2 new canonical competitors added: Credly (adjacent, digital credentials) and WooCommerce (diy, WordPress ecommerce)
- [x] `competitors.json` now has 19 competitors across 5 types (added WapCRM as adjacent, alias 'Wap' → 'WapCRM')
- [x] Fallback descriptions for unknown competitors: Fiducare hardcoded; future unknowns show "Description not available"
- [x] Competitor alias system: `_compAliases` map for legacy/abbreviated names in call data
- [x] Tab persistence via URL hash: `competitors` tab now included in `parseHash()` allowed list (reload preserves active tab)
- [x] Copy Transcript button on By Call tab (same pattern as Copy HubSpot Note, handles missing transcript)
- [x] Competitor overview cards: hover-reveal dismiss X button (top-right, turns red on hover, password-protected)
- [x] Unverified competitor state: unknown competitors shown with reduced opacity, dashed border, "Unverified" badge, sorted to end of grid
- [x] Pending analysis banner restyled: muted blue-gray background with blue left-border accent, pulsing count badge (replaces bright orange inline-styled banner)
- [x] Card-to-details navigation: clicking overview card smooth-scrolls to detail section, auto-expands if collapsed, flash highlight on arrival
- [x] Adaptive competitor mix: stacked bar for 10+ mentions and 4+ competitors; compact sorted list with inline bars for low-volume data
- [x] Details section polish: 16px mention gaps, left indent, rgba separator lines, 200ms expand/collapse animation
- [x] Colored type tags on detail cards: DIRECT (blue), LMS (green), DIY (gold), ADJACENT (purple), MARKETPLACE (red)
- [x] Segment comparison heat map: replaced dot indicators with color-intensity cells (rgba blue, opacity scales with count), numbers in light blue, 2px cell gaps, legend below
- [x] Feature tag capping: max 4 visible tags per call card, "+N more" expander. Smaller muted tags (11px, gray, no border)
- [x] Hover-only exclude button on call cards (opacity 0→1 on card hover)
- [x] Compact pending calls: single-line rows with PENDING badge, 60% opacity, gray left border
- [x] Segment label redesign: muted uppercase label above call title, segment color drives left border accent on card
- [x] Week header segment breakdown: colored dots with count + abbreviation (PT&D, CE&C, etc.) per week
- [x] Password gate redesign: custom modal with contextual action labels, shake animation on wrong password, Escape/backdrop to cancel. Replaces browser prompt/alert.
- [x] Password gate enlarged to final spec (460px wide, 36px padding, 20px lock in 48px circle, 18px header, 15px input/button)
- [x] Segment tags on competitor mentions: colored segment badge inline with each mention entry (company → segment → type → date)
- [x] Prospect-first titles on By Call tab: generic titles ("Connect With Teachable", "Teachable Followup") swapped for prospect/company name, original title as muted subtitle
- [x] New Since Last Visit system: localStorage tracks last dashboard visit, blue dot indicators on new cards, "NEW" badges on new competitor mentions, "N new" pill in header, "New Since Last Visit" date filter option
- [x] Feature detail breakout panel: detail content renders below the full 2-column row (not inside the card), preventing adjacent card height stretching. Only one detail panel open at a time. Same-row card click swaps content; different-row click collapses old + opens new. Timestamp badges, speaker quotes (truncated at 200 chars with "show full"), "Show N more" for 3+ mentions.
- [x] Clay.com v3 integration (lib/clay/): ICP snapshot generation, seed scoring, exclude list management, re-engagement detection, webhook-based export to Clay tables
- [x] Prospecting tab: sortable seed table, score bars, domain confidence indicators, inline export confirm, 3-card status panel (excludes, re-engagements, credit estimate)
- [x] Domain resolution pipeline: company_domain + domain_confidence (high/low/unresolved) in analysis, aggregation, and UI
- [x] Company aggregation: mention.company > marketing_data.company > title inference priority, self-company exclusion (Teachable)
- [x] Token-based auth: password gate on Generate Snapshot + all Clay/Mixmax write endpoints; read endpoints unauthenticated
- [x] Atomic snapshot writes via tempfile + os.replace()
- [x] Auto-snapshot generation after analyze_features.py inject (fail-soft)
- [x] Mixmax integration (lib/mixmax/): intelligence-driven email campaigns with prepare/enroll split
- [x] Mixmax mapper: hypothesis vs fact distinction — pre-built competitor_sentence and pain_point_sentence
- [x] Mixmax sent ledger: JSONL append-only file for cross-sequence dedup
- [x] Mixmax dry_run: true by default — no emails sent until explicitly enabled
- [x] Server binds to 127.0.0.1 (localhost only, not 0.0.0.0)
- [x] Null-safe duration parsing from Fireflies API (handles duration: null)
- [x] Copy Brief button: self-contained prompt copied to clipboard, paste into any Claude chat to generate emails
- [x] Multi-seed intelligence merging: select 2+ seeds, features ranked by frequency with [N/M seeds] tags, quotes from every source
- [x] Competitor aggregation across seeds: frequency count, "Patterns" summary for 2+ seed mentions
- [x] Confidence calibration: prompt tells Claude to match language strength to feature frequency
- [x] Feature-to-problem mapping: 35 features mapped to business problems, embedded in every Copy Brief
- [x] Outreach safety guardrails baked into prompt: banned phrases, hypothesis language, 100-word limit, peer-to-peer tone

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
analyze_features.py (Claude extracts features + categories + segments + marketing_data)
    |
    v
features.json  <-- CANONICAL DATA FILE
    |
    +---> dashboard_template.html (interactive dashboard, 6 tabs)
    |       By Feature | By Call | Personas | Competitors | Reports | Prospecting
    +---> sync_to_sheets.py (Google Sheets sync)
    +---> generate_reports.py (Friday cron)
    +---> lib/clay/ (ICP snapshot, seed scoring, exclude lists, Clay webhook export)
    +---> lib/mixmax/ (intelligence-driven email campaigns, prepare/enroll workflow)
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
  dashboard_template.html    # HTML template with 6 tabs (By Feature, By Call, Personas, Competitors, Reports, Prospecting)
  categories.json            # 10 feature category definitions
  segments.json              # 9 prospect segment definitions
  competitors.json           # 19 canonical competitors (5 types), with short_description
  CLAUDE.md                  # Auto-loaded instructions for Claude Code
  README.md                  # Setup instructions, workflow docs
  PROJECT_STATUS.md          # This file — read first, update at session end
  requirements.txt           # Python dependencies
  .env                       # API keys (not committed)
  .gitignore                 # Excludes credentials/, *.json (except categories.json), test_output/
  .feature_blacklist         # Excluded feature keywords
  .feature_names             # Canonical feature name cache
  lib/
    clay/                    # Clay.com v3 integration
      __init__.py            # Public API: generate_snapshot, get_seed_companies, exports
      transforms.py          # Company aggregation, ICP snapshot, seed selection, scoring
      scoring.py             # Score calculation with signals, boosts, max_score
      config.json            # Scoring config (signals, segment/feature boosts, credit controls)
      client.py              # Clay webhook HTTP client
      validator.py           # Payload validation for seeds/excludes/re-engagements
    mixmax/                  # Mixmax integration (intelligence-driven email campaigns)
      __init__.py            # Public API: prepare_enrollment, enroll_contacts, get_sequences
      client.py              # Mixmax REST API HTTP client (X-API-Token auth)
      mapper.py              # Intelligence-to-variables mapper (hypothesis vs fact)
      ledger.py              # JSONL sent ledger for cross-sequence dedup
      config.json            # Segment-to-sequence routing, dry_run default true
  credentials/               # OAuth credentials + cached token (gitignored)
  data/
    last_snapshot.json       # Latest ICP snapshot (gitignored)
    mixmax_sent_ledger.jsonl # Mixmax enrollment ledger (gitignored)
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
