# Teachable GTM Intelligence

A go-to-market intelligence tool for Teachable. It pulls sales-call transcripts from Fireflies and deal/pipeline data from HubSpot, uses Claude to extract structured intelligence (feature requests, personas, competitors, win/loss signals), and renders an interactive dashboard for the product, marketing, and sales teams. It also drives outbound: an ICP prospecting engine (Clay) and personalized sequence enrollment (Mixmax).

**Live dashboard:** deployed to Netlify (`test_output/index.html`, HTTP Basic-Auth).

---

## Dashboard tabs

| Tab | What it shows |
|-----|---------------|
| **By Feature** | Every extracted feature request, grouped and categorized, with company pills, quotes, and confidence. Detail breakout panel per feature. |
| **By Call** | Per-call cards: prospect-first titles, segment labels, feature tags, HubSpot note + transcript copy buttons, exclude/pending controls. |
| **Personas** | Prospect segmentation — segment overview cards, comparison heat map, mix bar, prospect cards by segment, PD/KB (Program Distributor vs Knowledge Business) classification. |
| **Competitors** | Competitor mentions across calls — mix bar, overview cards, type filters (direct/LMS/DIY/adjacent/marketplace), intel/differentiators, exclusion + review of off-taxonomy names. |
| **Reports** | Product Report + Marketing Report (segment breakdowns, feature recaps, quotes, objections, buying signals, timeline). |
| **Win/Loss** | Closed-won / closed-lost analysis from HubSpot — feature-impact rows, competitor-feature crosswalk, loss-reason summary, pricing/packaging signals. |
| **My Deals** | A rep's own deals with editable, auto-formatted customer-feedback notes (WYSIWYG, persisted locally). |
| **Performance** | Rep/pipeline performance from HubSpot (+ Mixmax/Fireflies) — open pipeline, meetings, activity, per-rep/per-day slices, targets. |
| **Prospecting** | Clay-powered ICP snapshot — seed companies with scores, segment analysis, competitor landscape, credit estimates, export controls. |

---

## Architecture

```
Fireflies API ──▶ retrieve_calls / scan ──┐
                                          ├─▶ analyze_features.py  ──▶ test_output/features.json  (canonical)
HubSpot (calls, notes, deals) ────────────┘        (Claude extracts             │
                                                    features, segments,          ├─▶ rebuild_dashboard.py ─▶ test_output/index.html ─▶ Netlify
                                                    competitors, PD/KB,          │
                                                    marketing data, notes)       └─▶ sync_to_sheets.py ─▶ Google Sheet (product team)

HubSpot Deals ──▶ fetch_lost_deals.py  ──▶ test_output/win_loss.json ──┐
HubSpot/Mixmax/FF ─▶ fetch_performance.py ─▶ test_output/performance.json ─┴─▶ embedded into index.html (WIN_LOSS / PERF)

Dashboard intelligence ──▶ lib/clay (ICP snapshot, seeds) ──▶ Clay ──▶ lib/mixmax (sequence enrollment) ──▶ Mixmax
```

`test_output/features.json` is the canonical data artifact. `rebuild_dashboard.py` renders the template with the feature data plus the win/loss and performance JSON embedded inline, so the deployed `index.html` is self-contained.

---

## Quick start

```bash
pip3 install -r requirements.txt
cp .env.example .env        # add API keys (see Environment below)
python3 server.py           # dashboard + APIs at http://localhost:8080
```

---

## Core workflows

### 1. Call analysis (Fireflies → features)

```bash
# Scan for new calls (dashboard "Scan" button, or the server scan API)
python3 analyze_features.py extract test_output/index.html   # print transcripts + analysis prompt
#   → Claude reads transcripts and writes a features JSON (features, notes,
#     marketing_data, segment_data, competitor_mentions) per call
python3 analyze_features.py validate features.json --fix     # canonicalize categories/segments/competitors
python3 analyze_features.py inject test_output/index.html features.json
python3 rebuild_dashboard.py                                 # re-render index.html
```

Extraction rules (enforced by the prompt + `validate`): prospect speakers only (never Teachable employees), exact canonical category/segment/competitor names, `NEEDS_REVIEW` for anything off-taxonomy, and a PD/KB classification per account. See `CLAUDE.md` for the full analysis protocol.

### 2. Win/Loss + Performance refresh (HubSpot)

```bash
python3 fetch_lost_deals.py --days 180 --out test_output/win_loss.json      # closed won/lost + feature signals
python3 fetch_performance.py --days 180 --out test_output/performance.json  # pipeline, meetings, activity
python3 rebuild_dashboard.py                                                 # embed into index.html
```

Both run automatically via GitHub Actions (see **Automation**). `fetch_lost_deals.py` uses HubSpot structured close-time fields by default; `--api-extract` adds Claude-based feature extraction from feedback notes.

### 3. Prospecting (Clay ICP)

The **Prospecting** tab generates an ICP snapshot from the analyzed call corpus: it aggregates companies, scores them against ICP signals, selects seed companies, and estimates enrichment credits. Export/push actions are password-gated (`CLAY_DASHBOARD_PASSWORD`). Module: `lib/clay/`.

### 4. Outreach (Mixmax)

`lib/mixmax/` maps dashboard intelligence to personalized sequence variables (competitor/pain-point sentences, hypothesis vs. fact) and enrolls contacts into Mixmax sequences, with a JSONL ledger for cross-sequence dedup. Enrollment is Bearer-token gated and dry-run by default.

### 5. Product-team sync (Google Sheets)

```bash
python3 sync_to_sheets.py --setup     # first-time OAuth + sheet setup
python3 sync_to_sheets.py --dry-run   # preview
python3 sync_to_sheets.py             # sync (upsert)
```

Syncs feature mentions to a Google Sheet with formula-driven summary tabs (Feature Summary, Category Rollup, Company View) and an audit log. Also available via the dashboard "Sync to Sheets" button and the Friday cron in `generate_reports.py`.

<details>
<summary>Google Sheets first-time setup</summary>

1. In [Google Cloud Console](https://console.cloud.google.com/): create a project and enable the **Google Sheets API**.
2. Create **OAuth client ID** → **Desktop app**; save the JSON to `credentials/oauth_credentials.json`.
3. Create a Google Sheet; copy its ID (between `/d/` and `/edit`) into `.env` as `GOOGLE_SHEETS_SPREADSHEET_ID`.
4. Run `python3 sync_to_sheets.py --setup` (opens a browser once; token cached at `credentials/token.json`).

Sheet columns A–N are sync-owned; O–R (Status, Priority, Jira Link, Notes) are for the product team and are preserved across syncs.
</details>

---

## Taxonomy

Claude assigns each item an exact canonical name from these config files (the source of truth), validated at inject time with fuzzy-match auto-correction and `NEEDS_REVIEW` escalation:

- **`categories.json`** — feature categories (Curriculum & Content, Assessments & Quizzes, Commerce & Checkout, Compliance & Credentialing, Reporting & Analytics, Engagement & Community, Organizations & Multi-tenancy, Platform & Integrations, Design & Branding, User Management).
- **`segments.json`** — prospect segments (CE & Credentialing, Professional Training, Course Creators, Associations, Academic, Corporate Education, Health & Wellness, Government, etc.).
- **`competitors.json`** — canonical competitors across 5 types (direct / LMS / DIY / adjacent / marketplace), with descriptions and aliases.
- **`config/capability_map.json`**, **`config/company_aliases.json`**, **`feature_consolidation_map.json`** — feature→capability mapping, company-name normalization, and duplicate-name consolidation.

Confidence scores accompany each feature (High ≥ 0.85 / Medium 0.65–0.84 / Low < 0.65); low-confidence rows are flagged in the dashboard and Google Sheet.

---

## Automation (GitHub Actions)

| Workflow | Schedule | Action |
|----------|----------|--------|
| `refresh-win-loss.yml` | Mondays 9am ET | `fetch_lost_deals.py --days 180 --api-extract` |
| `refresh-performance.yml` | (cron) | `fetch_performance.py` |
| `sync_performance.yml` | daily / weekly | performance sync |
| `generate_reports.py` | Friday cron | flag pending reports + sync Sheets |

---

## Deployment

Netlify serves `test_output/` statically (`netlify.toml`, `publish = "test_output"`) behind HTTP Basic-Auth. The site's production branch is `main` — merging to `main` triggers an auto-deploy. `index.html` embeds all data inline (features, win/loss, performance), so no runtime data fetch is required.

---

## Environment

```
ANTHROPIC_API_KEY=            # Claude (optional AI extraction paths)
FIREFLIES_API_KEY=           # call transcripts
HUBSPOT_TOKEN=               # deals, pipeline, notes (win/loss + performance)
GOOGLE_SHEETS_SPREADSHEET_ID= # product-team sync
CLAY_DASHBOARD_PASSWORD=     # gates credit-costing Prospecting exports
MIXMAX_API_TOKEN[_REP]=      # sequence enrollment (per-rep tokens supported)
```

Secrets live in `.env` (gitignored); `credentials/` holds Google OAuth tokens (gitignored).

---

## Key files

| File | Purpose |
|------|---------|
| `server.py` | Flask server — scan, analysis, Clay, Mixmax, and sync APIs; serves the dashboard |
| `dashboard_template.html` | Single-file dashboard UI (9 tabs, `{{DATA_JSON}}` / `{{WIN_LOSS_JSON}}` / `{{PERFORMANCE_JSON}}` injection) |
| `analyze_features.py` | Extract / validate / inject / cleanup / consolidate workflow |
| `rebuild_dashboard.py` | Render `index.html` from `features.json` + win/loss + performance |
| `fetch_lost_deals.py` | HubSpot closed won/lost → `win_loss.json` |
| `fetch_performance.py` | HubSpot/Mixmax/Fireflies → `performance.json` |
| `sync_to_sheets.py` | Google Sheets sync (upsert, summary tabs, audit log) |
| `retrieve_calls.py`, `client.py` | Fireflies retrieval + API client |
| `lib/clay/` | Clay ICP snapshot, seed selection, scoring, client, validation |
| `lib/mixmax/` | Mixmax mapper, client, dedup ledger |
| `categories.json`, `segments.json`, `competitors.json` | Canonical taxonomy |
| `test_output/features.json` | Canonical data artifact (written by inject) |
| `test_output/index.html` | Rendered dashboard (served by Flask + Netlify) |
| `CLAUDE.md`, `PROJECT_STATUS.md` | Analysis protocol + cross-session status |
