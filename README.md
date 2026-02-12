# Teachable Sales Intelligence

Pulls sales call transcripts from Fireflies, extracts feature requests using Claude, and syncs them to a Google Sheet for the product team.

## Quick Start

```bash
pip3 install -r requirements.txt
cp .env.example .env  # Add your API keys
python3 server.py     # Dashboard at http://localhost:8080
```

## Workflow

1. **Scan** - Pull new calls from Fireflies (dashboard UI or CLI)
2. **Extract** - Print transcripts for Claude analysis: `python3 analyze_features.py extract test_output/dashboard.html`
3. **Analyze** - Claude reads transcripts, outputs features JSON with categories
4. **Inject** - Write features into dashboard: `python3 analyze_features.py inject test_output/dashboard.html features_output.json`
5. **Sync** - Push to Google Sheets: `python3 sync_to_sheets.py` or use the dashboard button

## Google Sheets Setup

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (e.g. "teachable-sales-intelligence")
3. Enable the **Google Sheets API**

### 2. Create OAuth Desktop Credentials

1. APIs & Services > Credentials > Create Credentials > **OAuth client ID**
2. Application type: **Desktop app**
3. Download the JSON file and save it to `credentials/oauth_credentials.json`

### 3. Create the Sheet

1. Create a new Google Sheet: "Sales Intelligence - Feature Requests"
2. Copy the spreadsheet ID from the URL (the long string between `/d/` and `/edit`)

### 4. Configure .env

```
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id_here
```

### 5. Initial Setup

```bash
python3 sync_to_sheets.py --setup   # Opens browser for Google sign-in on first run
python3 sync_to_sheets.py --dry-run # Preview what would sync
python3 sync_to_sheets.py           # First sync
```

On the first run, a browser window will open for Google sign-in. After authorizing, the token is cached at `credentials/token.json` and subsequent runs won't need the browser.

## Sync Commands

| Command | Description |
|---------|-------------|
| `python3 sync_to_sheets.py` | Sync latest data |
| `python3 sync_to_sheets.py --dry-run` | Preview without writing |
| `python3 sync_to_sheets.py --force` | Update all rows |
| `python3 sync_to_sheets.py --setup` | First-time sheet setup |
| `python3 analyze_features.py inject ... --sync-sheets` | Inject + sync |

The dashboard also has a "Sync to Sheets" button on the By Feature tab, and the Friday cron (`generate_reports.py`) auto-syncs.

## Sheet Structure

**Feature Requests** (raw data) - Columns A-N are sync-owned, O-R are for the product team:

| Sync-Owned (A-N) | Product Team (O-R) |
|---|---|
| Mention ID, Feature, Category, Company, Contact, Contact Title, Request Type, Quote, Timestamp, Recording, Call Title, Call Date, Week, Confidence | Status, Priority, Jira Link, Notes |

**Summary tabs** (formula-driven, auto-update):
- Feature Summary - unique features with mention counts and companies
- Category Rollup - category-level aggregation
- Company View - per-company feature requests

**Sync Log** - audit trail of every sync run

## Confidence Scores

Every feature extracted from a call gets a confidence score for its category assignment.

| Label | Score Range | Meaning | Action |
|-------|------------|---------|--------|
| High | 0.85 - 1.0 | Category is almost certainly correct | No action needed |
| Medium | 0.65 - 0.84 | Probably correct, minor ambiguity | Worth a glance |
| Low - verify | < 0.65 | Category may be wrong | Check the quote, reassign if needed |

Low-confidence rows are highlighted yellow in the Google Sheet.

## Feature Categories

Defined in `categories.json` (10 categories):

- Curriculum & Content
- Assessments & Quizzes
- Commerce & Checkout
- Compliance & Credentialing
- Reporting & Analytics
- Engagement & Community
- Organizations & Multi-tenancy
- Platform & Integrations
- Design & Branding
- User Management

Categories are assigned by Claude during transcript analysis and included in the analysis prompt. The `categories.json` file is the source of truth.

## Key Files

| File | Purpose |
|------|---------|
| `server.py` | Flask dev server, scan API, sync endpoint |
| `dashboard_template.html` | Dashboard UI template |
| `analyze_features.py` | Extract/normalize/inject workflow |
| `sync_to_sheets.py` | Google Sheets sync with upsert |
| `categories.json` | Feature category definitions |
| `generate_reports.py` | Friday cron: flag pending reports + sync |
| `test_output/features.json` | Canonical data file (written by inject) |
| `test_output/dashboard.html` | Rendered dashboard with embedded data |
