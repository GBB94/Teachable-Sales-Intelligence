# Fireflies Call Retriever

A robust, flexible layer for pulling Fireflies call transcripts with powerful filtering, HubSpot-ready note generation, and feature request tracking.

## Features

- **Multiple Filter Types**: Date ranges, ownership, attendees, keywords, duration
- **Smart Filtering**: OR logic for keywords, AND logic across filter types
- **Improved Pagination**: Keeps fetching until the requested number of *filtered* results are collected
- **Retry Logic**: Automatic retry with backoff on API failures
- **HubSpot Notes**: Generate copy/paste-ready call notes for HubSpot
- **Feature Request Tracking**: Scan transcripts for feature mentions with frequency counts
- **Export Options**: JSON, CSV, HubSpot notes, feature request reports
- **User Discovery**: Find all users/emails in your Fireflies account
- **Type Safety**: Structured dataclasses for clean data handling

## Quick Start

```bash
# Install dependencies
pip install requests

# Set your API key
export FIREFLIES_API_KEY="your_api_key_here"

# Get all calls from last 30 days
python retrieve_calls.py --days 30

# List all users to find emails
python retrieve_calls.py --list-users
```

## Usage Examples

### Basic Retrieval

```bash
# Last 30 days
python retrieve_calls.py --days 30

# Last 60 days, limit to 50 calls
python retrieve_calls.py --days 60 --limit 50
```

### Filter by Ownership

```bash
# Calls organized by you
python retrieve_calls.py --owner zach@teachable.com --days 90

# Multiple owners
python retrieve_calls.py --owner zach@teachable.com jane@teachable.com --days 90

# Calls with specific attendee(s)
python retrieve_calls.py --attendee prospect@company.com --days 30
python retrieve_calls.py --attendee prospect@company.com partner@org.com --days 30
```

### Filter by Keywords

```bash
# Sales calls (title contains "demo", "discovery", or "sales")
python retrieve_calls.py --title-keywords demo discovery sales --days 30

# Calls mentioning "pricing" in transcript
python retrieve_calls.py --transcript-keywords pricing --days 30

# Combine title and transcript keywords
python retrieve_calls.py --title-keywords demo --transcript-keywords teachable certificate --days 60
```

### Filter by Duration

```bash
# Calls longer than 30 minutes (1800 seconds)
python retrieve_calls.py --min-duration 1800 --days 30

# Calls between 15-45 minutes
python retrieve_calls.py --min-duration 900 --max-duration 2700 --days 30
```

### Complex Filters

```bash
# Your sales demos with prospects, longer than 20 minutes
python retrieve_calls.py \
  --owner zach@teachable.com \
  --title-keywords demo discovery \
  --min-duration 1200 \
  --days 90

# Calls mentioning "continuing education" with specific attendee
python retrieve_calls.py \
  --attendee prospect.com \
  --transcript-keywords "continuing education" certification \
  --days 60
```

### Export Data

```bash
# Export to JSON and CSV
python retrieve_calls.py --days 30 --export sales_calls

# This creates:
#   sales_calls.json (full data including transcripts)
#   sales_calls.csv (summary metadata)
```

### HubSpot Notes

Generate structured call notes you can copy/paste directly into HubSpot contact records.

```bash
# Generate notes for last 30 days of calls
python retrieve_calls.py --days 30 --hubspot-notes my_notes.txt

# Combine with filters for targeted notes
python retrieve_calls.py \
  --owner zach@teachable.com \
  --title-keywords demo discovery \
  --days 60 \
  --hubspot-notes demo_notes.txt
```

Each note is formatted as:

```
CALL: Demo with Acme Corp
DATE: 2024-02-01  |  DURATION: 30 min
ORGANIZER: zach@teachable.com
ATTENDEES: John Smith (john@acme.com), Jane Doe (jane@acme.com)
---
SUMMARY
Discussed Teachable platform for their CE program...
---
ACTION ITEMS
- Send pricing proposal by Friday
- Schedule follow-up with compliance team
---
KEY TOPICS
continuing education, certification, pricing, compliance
---
TRANSCRIPT: https://app.fireflies.ai/view/...
```

### Feature Request Tracking

Scan call transcripts for feature request mentions and see what prospects are asking for most.

```bash
# Scan last 60 days of calls for feature requests
python retrieve_calls.py --days 60 --feature-requests

# Scan and export the report to JSON + CSV
python retrieve_calls.py --days 90 --feature-export feature_log

# This creates:
#   feature_log.json  (full report with context snippets)
#   feature_log.csv   (spreadsheet-friendly, one row per mention)
```

The scanner checks for phrases like "feature request", "do you support", "is there a way to", plus CE-specific terms like "SCORM", "certificate automation", "compliance reporting", and more. You can customize the keyword list in the code.

Output looks like:

```
 FEATURE REQUEST REPORT
======================================================================
  Total mentions:  47
  Across calls:    12
  Generated:       2024-02-11T14:30:00+00:00

  Top requested (by mention count):
    12x  certificate automation             ████████████
     8x  compliance reporting               ████████
     7x  SSO                                ███████
     5x  SCORM                              █████
     4x  API access                         ████
     3x  white label                        ███

  Recent mentions:
    [2024-02-08] Demo with State Board of Nursing
      Jane Smith: "Is there a way to automatically generate certificates when..."
      Matched: certificate automation
```

### Full Pipeline

```bash
# Filter, export, generate HubSpot notes, and scan features in one command
python retrieve_calls.py \
  --owner zach@teachable.com \
  --title-keywords demo discovery sales \
  --days 90 \
  --export q1_sales \
  --hubspot-notes q1_hubspot.txt \
  --feature-export q1_features
```

### Discover Users

```bash
python retrieve_calls.py --list-users
```

## Programmatic Usage

```python
from fireflies_retriever import FirefliesRetriever, CallFilter

retriever = FirefliesRetriever(api_key="your_key")

# Get calls
calls = retriever.get_calls(
    filter_criteria=CallFilter(
        days_back=90,
        owner_emails=["zach@teachable.com"],
        title_keywords=["demo", "discovery"],
        min_duration=900,
        limit=50
    )
)

# Generate HubSpot notes
for call in calls:
    note = call.to_hubspot_note()
    print(note)  # or copy to clipboard, push to HubSpot API, etc.

# Scan for feature requests
report = retriever.scan_feature_requests(calls)
report.print_summary()

# Custom keywords
report = retriever.scan_feature_requests(
    calls,
    keywords=["SCORM", "certificate automation", "API access", "white label"]
)

# Export everything
retriever.export_to_json(calls, "calls.json")
retriever.export_to_csv(calls, "calls.csv")
retriever.export_hubspot_notes(calls, "notes.txt")
retriever.export_feature_report(report, "features.json")
retriever.export_feature_report_csv(report, "features.csv")
```

## Filter Options Reference

```python
CallFilter(
    # Date filtering
    days_back=30,              # Look back N days from now
    start_date=datetime(...),  # Explicit start date
    end_date=datetime(...),    # Explicit end date

    # Ownership filtering
    owner_emails=["email@domain.com"],     # Filter by organizer(s)
    attendee_emails=["email@domain.com"],  # Filter by attendee(s)

    # Keyword filtering (OR logic within each list)
    title_keywords=["demo", "sales"],           # Keywords in title
    transcript_keywords=["pricing", "budget"],  # Keywords in transcript

    # Duration filtering
    min_duration=900,          # Minimum duration (seconds)
    max_duration=3600,         # Maximum duration (seconds)

    # Pagination
    limit=100,                 # Max *filtered* calls to retrieve
    skip=0                     # Skip first N raw calls
)
```

## Tips

1. **Start with User Discovery**: Run `--list-users` first to see available emails
2. **Partial Matches Work**: `--owner zach` will match "zach@teachable.com"
3. **Keywords are OR Logic**: `--title-keywords demo sales` matches calls with EITHER word
4. **Combine Filters**: All filter types work together with AND logic
5. **Export Often**: Use `--export` to save results for later analysis
6. **Transcript Keywords are Slower**: They require searching full transcripts
7. **HubSpot Notes**: Use `--hubspot-notes` to create ready-to-paste call records
8. **Track Feature Requests**: Use `--feature-requests` to see what prospects ask for most
9. **Full Pipeline**: Combine `--export`, `--hubspot-notes`, and `--feature-export` in one command
