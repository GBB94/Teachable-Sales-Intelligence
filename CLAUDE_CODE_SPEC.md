# CLAUDE CODE BUILD SPEC: Fireflies Call Retriever

## What this project is
A Python tool that pulls call transcripts from the Fireflies.ai API, generates HubSpot-ready call notes, and tracks feature requests mentioned by prospects. It includes an interactive HTML dashboard for reviewing feature requests across calls.

## Who uses this
Zach McCall on the Teachable sales team. He runs demos and discovery calls with continuing education / credentialing providers. He needs to:
1. Pull call transcripts from Fireflies
2. Get copy/paste-ready notes for HubSpot
3. See what features prospects are asking for most, with timestamps so he can jump back into the recording

## Files in this project

### fireflies_retriever.py (core library)
The main module. Contains:
- `CallFilter` dataclass for filtering calls by date, owner, attendee, keywords, duration
- `Call` dataclass with `.to_hubspot_note()` method for generating structured notes
- `FeatureRequest` dataclass with timestamp + deep link fields
- `FeatureRequestReport` dataclass with grouped printing and aggregation
- `FirefliesRetriever` class with:
  - `get_calls()` - fetch + filter calls from Fireflies GraphQL API
  - `scan_feature_requests()` - scan transcripts for feature mentions, excluding internal speakers
  - `export_to_json()`, `export_to_csv()`, `export_hubspot_notes()`
  - `export_feature_dashboard()` - generates interactive HTML dashboard

### retrieve_calls.py (CLI)
Argparse CLI that wraps the library. Key flags:
- `--days`, `--owner`, `--attendee`, `--title-keywords`, `--transcript-keywords`
- `--hubspot-notes <file>` - export HubSpot notes
- `--feature-requests` - scan for feature mentions
- `--dashboard <file>` - generate HTML dashboard
- `--exclude-domains`, `--exclude-speakers` - filter internal team from feature tracking

### examples.py
Interactive walkthrough of all features.

### RETRIEVER_README.md
Full usage documentation.

## Key design decisions

### Internal speaker exclusion
The feature scanner excludes Teachable team members so that when Zach says "we support certificate automation" during a demo, it doesn't get counted as a prospect request. Defaults:
- Excluded domains: `teachable.com`
- Excluded speakers: `zach mccall`, `kevin`, `jerome`
Speaker matching is case-insensitive substring. It also resolves speaker names to attendee emails to check domains.

### Title keyword matching
Uses normalized matching that treats hyphens, underscores, and spaces as equivalent. So "followup" matches "follow-up", "follow up", "follow_up".

### Pagination
Keeps fetching from the API until the requested number of *filtered* results are collected (not raw results). Has a 10x safety cap.

### Timestamps and deep links
Every feature mention captures `start_time` from the Fireflies sentence data and builds a deep link: `transcript_url?t=<seconds>`. This lets the dashboard link directly to that moment in the recording.

### Dashboard (HTML)
Single self-contained HTML file with embedded JSON data. Two views:
1. **By Feature tab**: Features ranked by mention count. Click to expand and see every mention with timestamp, speaker, quote snippet, and a clickable link to jump to that moment in the Fireflies recording.
2. **By Call tab**: Calls sorted by date. Click to expand and see which features came up in that call, with colored feature tags and timestamped mentions. Includes "Open full transcript" link.

Both views have a search bar that filters across all visible content.

## How to run

```bash
export FIREFLIES_API_KEY="your_key"

# Full pipeline
python retrieve_calls.py \
  --owner zach@teachable.com \
  --title-keywords demo discovery sales teachable followup \
  --days 90 \
  --hubspot-notes notes.txt \
  --dashboard features.html \
  --feature-export feature_log

# Just the dashboard
python retrieve_calls.py --days 60 --dashboard features.html
```

## Dependencies
- Python 3.8+
- `requests` (pip install requests)
- No other external dependencies

## Things to know if modifying this code
- All datetimes are timezone-aware (UTC). Do not use naive `datetime.now()`.
- The Fireflies GraphQL API returns `sentences` with `start_time` and `end_time` as floats (seconds from start of recording).
- `_make_request()` has retry logic (2 attempts with exponential backoff).
- The `DEFAULT_FEATURE_KEYWORDS` list at the top of `fireflies_retriever.py` is the master keyword list. Edit it to add/remove tracked features.
- The `DEFAULT_EXCLUDE_DOMAINS` and `DEFAULT_EXCLUDE_SPEAKERS` lists on the `FirefliesRetriever` class control who gets excluded from feature tracking.
- The dashboard HTML uses double-brace `{{` escaping because it's inside a Python f-string.
