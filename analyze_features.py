#!/usr/bin/env python3
"""
AI feature analysis helper for Claude Code workflow.

Three-step process:
  1. `extract`   — reads a dashboard HTML file, prints call transcripts for review
  2. `normalize` — merges similar feature names in a features JSON file
  3. `inject`    — takes a features JSON file, rewrites the dashboard + HubSpot notes

Usage (inside Claude Code):
  python3 analyze_features.py extract test_output/dashboard.html [--prior old_dashboard.html]
  # ... Claude Code reads transcripts, builds features.json ...
  python3 analyze_features.py normalize features.json --merge-map merge.json
  python3 analyze_features.py inject test_output/dashboard.html features.json [--notes test_output/notes.txt]
"""

import argparse
import hashlib
import json
import os
import re
import sys


CANONICAL_NAMES_FILE = ".feature_names"


def generate_mention_id(call_id: str, feature_name: str) -> str:
    """Generate a stable, deterministic ID for a mention."""
    raw = f"{call_id}|{feature_name}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def write_canonical_json(data: dict, output_dir: str):
    """Write the canonical features.json that all downstream tools read from."""
    path = os.path.join(output_dir, "features.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote canonical data to {path}")


def _load_canonical_names() -> list:
    """Load canonical feature names from cache file."""
    try:
        with open(CANONICAL_NAMES_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def _save_canonical_names(names: list):
    """Save canonical feature names to cache file."""
    unique = sorted(set(names))
    with open(CANONICAL_NAMES_FILE, "w") as f:
        for name in unique:
            f.write(name + "\n")
    print(f"Saved {len(unique)} canonical feature names to {CANONICAL_NAMES_FILE}")


def _get_feature_names_from_dashboard(html_path: str) -> list:
    """Extract unique feature names from a dashboard HTML file."""
    data = _extract_data_from_html(html_path)
    mentions = data.get("mentions", [])
    return sorted(set(m.get("keyword", "") for m in mentions if m.get("keyword")))


def _extract_data_from_html(html_path: str) -> dict:
    """Parse the embedded DATA JSON from a dashboard HTML file."""
    with open(html_path, "r") as f:
        html = f.read()

    # The data is on a line like: const DATA = {...};
    match = re.search(r"const DATA = ({.*?});\s*$", html, re.MULTILINE | re.DOTALL)
    if not match:
        print("Error: Could not find DATA JSON in dashboard HTML.")
        sys.exit(1)

    # The JSON can be very large; find the correct end boundary
    # We know it starts with { and the line ends with };
    # Use a more targeted approach: find "const DATA = " and parse from there
    start_marker = "const DATA = "
    start_idx = html.index(start_marker) + len(start_marker)
    # Find the matching end — the JSON object ends with }; on the same logical line
    # We'll use json.JSONDecoder to find the end
    decoder = json.JSONDecoder()
    data, end_idx = decoder.raw_decode(html, start_idx)
    return data


def _write_data_to_html(html_path: str, data: dict):
    """Rewrite the dashboard HTML with updated DATA JSON."""
    with open(html_path, "r") as f:
        html = f.read()

    data_json = json.dumps(data)

    # Replace the const DATA = ...; line
    start_marker = "const DATA = "
    start_idx = html.index(start_marker)
    # Find the end of the JSON + semicolon
    json_start = start_idx + len(start_marker)
    decoder = json.JSONDecoder()
    _, json_end = decoder.raw_decode(html, json_start)
    # Skip past the semicolon
    semi_idx = html.index(";", json_end)

    new_html = html[:start_idx] + start_marker + data_json + html[semi_idx:]

    with open(html_path, "w") as f:
        f.write(new_html)


def cmd_extract(args):
    """Extract and print call transcripts from dashboard HTML.

    By default only extracts calls with pending_analysis=true.
    Use --all to extract every call.
    """
    # Load prior feature names for context
    prior_names = _load_canonical_names()
    if args.prior:
        dashboard_names = _get_feature_names_from_dashboard(args.prior)
        for name in dashboard_names:
            if name not in prior_names:
                prior_names.append(name)

    if prior_names:
        print(f"{'='*70}")
        print(f"ESTABLISHED FEATURE NAMES ({len(prior_names)}):")
        print("Reuse these names when a customer is asking for the same thing.")
        print(f"{'─'*70}")
        for name in sorted(prior_names):
            print(f"  - {name}")
        print()

    data = _extract_data_from_html(args.dashboard)
    all_calls = data.get("calls", [])

    # Determine which calls already have features extracted
    analyzed_ids = {m.get("call_id") for m in data.get("mentions", [])}

    if args.all:
        calls = all_calls
        print(f"Found {len(calls)} total calls in dashboard.\n")
    else:
        # Only extract calls that are pending analysis
        calls = [c for c in all_calls if c.get("pending_analysis") or c.get("id") not in analyzed_ids]
        pending_count = len([c for c in all_calls if c.get("pending_analysis")])
        unanalyzed_count = len(calls) - pending_count
        print(f"Found {len(all_calls)} calls total, {len(calls)} need analysis "
              f"({pending_count} pending, {unanalyzed_count} unanalyzed).\n")
        if not calls:
            print("All calls have been analyzed. Use --all to re-extract everything.")
            return

    for i, call in enumerate(calls, 1):
        transcript = call.get("transcript_text", "")
        word_count = len(transcript.split()) if transcript else 0

        status = ""
        if call.get("pending_analysis"):
            status = " [PENDING]"
        elif call.get("id") not in analyzed_ids:
            status = " [UNANALYZED]"

        print(f"{'='*70}")
        print(f"[{i}] {call['title']}{status}")
        print(f"    Date: {call.get('date', 'N/A')}  |  Duration: {call.get('duration', 0)} min")
        print(f"    ID: {call.get('id', 'N/A')}")
        print(f"    Transcript: {word_count} words")

        if transcript and not args.titles_only:
            print(f"{'─'*70}")
            # Print first 3000 chars to keep output manageable
            if len(transcript) > 3000:
                print(transcript[:3000])
                print(f"\n    ... [{len(transcript) - 3000} more chars truncated]")
            else:
                print(transcript)
        print()

    # Also write a machine-readable extract for convenience
    extract_path = os.path.join(os.path.dirname(args.dashboard), "calls_for_analysis.json")
    extract_data = []
    for call in calls:
        extract_data.append({
            "id": call.get("id"),
            "title": call.get("title"),
            "date": call.get("date"),
            "duration": call.get("duration"),
            "transcript": call.get("transcript_text", ""),
            "transcript_url": call.get("transcript", ""),
        })
    with open(extract_path, "w") as f:
        json.dump(extract_data, f, indent=2)
    print(f"Wrote {extract_path} for analysis.\n")

    # Print analysis instructions and expected output format for CC
    print(f"{'='*70}")
    print("DATA SOURCE")
    print(f"{'─'*70}")
    print("""The transcripts above are already saved in the dashboard data. Each call
has a transcript_text field with the full transcript. Do NOT re-pull from
the Fireflies API. Just read the transcripts printed above (or from
test_output/calls_for_analysis.json).

Only pull from Fireflies when scanning for NEW calls that aren't already
in the data (via the server scan UI or CLI).
""")

    print(f"{'='*70}")
    print("ANALYSIS PROMPT")
    print(f"{'─'*70}")
    print("""Read each sales call transcript carefully IN ITS ENTIRETY. Identify every
product feature, capability, or topic discussed in the context of what the
customer needs, is evaluating, or is interested in.

Include:
- Features the customer explicitly asks for ("we need X")
- Features the customer asks questions about ("how do your quizzes work?")
- Features discussed as part of the customer's use case or requirements
- Existing features the customer wants customized or improved
- Features the rep demos or pitches that the customer engages with
- Topics referenced in the call title or meeting agenda
- Capabilities the customer compares to their current/competing platform
- Pain points that imply a missing feature ("it's so manual", "we can't do X")

Do NOT include:
- Small talk, scheduling, or logistics
- Generic platform questions ("how much does it cost?") unless tied to a
  specific feature
- Internal Teachable discussion not relevant to a product capability

Be thorough. If in doubt, include it. A shallow analysis that misses
features discussed on the call is worse than a slightly long list.
""")

    # Load and print categories for the analysis prompt
    categories_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
    if os.path.exists(categories_path):
        with open(categories_path, "r") as f:
            cats = json.load(f)
        print(f"{'='*70}")
        print("FEATURE CATEGORIES")
        print(f"{'─'*70}")
        print("Assign EXACTLY ONE of these categories to each feature.\n")
        for cat in cats.get("categories", []):
            examples = ", ".join(cat.get("examples", [])[:4])
            print(f"  {cat['name']}")
            print(f"    {cat['description']}")
            print(f"    Examples: {examples}")
            print()
        print("Choose the single best-fit category. Do NOT use 'Other' — every")
        print("feature must map to one of the categories above.\n")

    print(f"{'='*70}")
    print("OUTPUT FORMAT")
    print(f"{'─'*70}")
    print("Write a JSON file with this structure:")
    print("""
{
  "features": {
    "<call_id>": [
      {
        "feature": "Short Normalized Feature Name",
        "category": "Category Name (from list above)",
        "speaker": "Customer Name (Company)",
        "quote": "most relevant 1-2 sentence verbatim quote",
        "timestamp": "~MM:SS",
        "ts_seconds": 123,
        "type": "prospect_request | prospect_interest | rep_highlighted"
      }
    ]
  },
  "notes": {
    "<call_id>": "full HubSpot note text (see format below)"
  },
  "recap": "optional weekly recap paragraph",
  "company_summaries": { "Company": "one-line summary" }
}

Feature type meanings:
  "prospect_request"  — customer explicitly asked for this feature
  "prospect_interest" — customer asked about it or engaged positively
  "rep_highlighted"   — rep pitched/demoed it and customer showed interest
""")
    print("HUBSPOT NOTE FORMAT (for the 'notes' field):")
    print(f"{'─'*70}")
    print("""CALL DATE: YYYY-MM-DD
ATTENDEES: Name (email), Name (email)
COMPANY: Company Name
STAGE: Discovery / Demo / Followup / Negotiation / Closed

---
SUMMARY
2-3 sentences: who they are, what they want to build, where they are in evaluation

---
USE CASE
Primary goal:
Audience:
Business model:
Content types:
Scale expectations:

---
QUALIFICATION
Budget:
Authority:
Need:
Timeline:

---
TECHNICAL REQUIREMENTS
Integrations:
Reporting needs:
Payments / checkout:
Admin or seat management:
Special workflows or constraints:

---
BUYING SIGNALS
- specific signal observed

---
RISKS / OBJECTIONS
- specific risk

---
PRODUCT FEEDBACK
- specific feedback

---
PRICING DISCUSSED
Plan discussed:
Discounts offered:
Contract length discussed:
Constraints or approvals needed:

---
NEXT STEPS
Zach:
- action item

Customer:
- action item

Scheduled:
- Next meeting date:
- Materials to send:

---
ADDITIONAL CONTEXT
Personality, internal politics, seriousness level, gut feel

---
TRANSCRIPT: {fireflies_url}

For short calls (<10 min), use compact version:
Summary, Use Case, Qualification, Risks, Next Steps only.
Skip empty sections — don't write "Not discussed."
""")
    print(f"{'='*70}")
    print("MARKETING REPORT FORMAT")
    print(f"{'─'*70}")
    print("""In addition to features and notes, generate a marketing_report object.
This is for the marketing team — a different lens on the same call data.

IMPORTANT RULE: Do NOT fabricate or infer any information. Only include
data that is EXPLICITLY stated in the call transcripts. If a section has
no data from the calls, leave the array empty — the dashboard will show
"No data from this week's calls" automatically. All quotes must be
VERBATIM from transcripts with attribution.

Add this to the top-level JSON output:

  "marketing_report": {
    "persona_profiles": [
      {
        "name": "Contact Name",
        "title": "Their Title (only if stated)",
        "company": "Company name and brief description from transcript context",
        "industry": "Only if mentioned in the call",
        "role_in_decision": "champion / influencer / decision-maker (only if clear)",
        "team": ["Other people mentioned or on the call"]
      }
    ],
    "voice_of_customer": {
      "problem_descriptions": [
        {
          "quote": "Exact verbatim quote of how they describe their problem",
          "speaker": "Speaker Name",
          "call": "Call Title"
        }
      ],
      "terminology": ["specific words/phrases prospects used"],
      "frequent_questions": ["questions prospects asked on calls"]
    },
    "pain_points": [
      {
        "pain": "Short pain point description",
        "company": "Company Name",
        "quote": "Brief verbatim quote",
        "current_workaround": "What they said they're currently using (only if stated)",
        "business_impact": "Only if they stated it"
      }
    ],
    "objections": {
      "items": [
        {
          "objection": "The objection",
          "company": "Company Name",
          "quote": "Brief verbatim quote"
        }
      ],
      "competitors_mentioned": ["Only names explicitly said in calls"],
      "barriers": ["Barriers to adoption mentioned (only if stated)"]
    },
    "buying_signals": [
      {
        "trigger": "What triggered their interest (only if stated)",
        "company": "Company Name",
        "timeline": "Only if discussed",
        "budget_feedback": "Only if discussed"
      }
    ],
    "success_metrics": [
      {
        "metric": "How they measure success (only if stated)",
        "company": "Company Name",
        "quote": "Verbatim quote if available"
      }
    ],
    "weekly_summary": {
      "patterns": ["Only patterns clearly visible in the data"],
      "surprising_insights": ["Anything unexpected from the calls"]
    }
  }
""")


def cmd_inject(args):
    """Inject AI-analyzed features into dashboard HTML and optionally notes."""
    # Load features JSON — supports two formats:
    #   Old: { "call_id": [...features...] }
    #   New: { "features": { "call_id": [...] }, "notes": { "call_id": "full note text" } }
    with open(args.features_json, "r") as f:
        raw = json.load(f)

    if "features" in raw and isinstance(raw["features"], dict):
        features_by_call = raw["features"]
        notes_by_call = raw.get("notes", {})
        recap_text = raw.get("recap", "")
        company_summaries = raw.get("company_summaries", {})
        marketing_report = raw.get("marketing_report", {})
    else:
        features_by_call = raw
        notes_by_call = {}
        recap_text = ""
        company_summaries = {}
        marketing_report = {}

    # Load categories list for validation (optional fallback)
    valid_categories = set()
    categories_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
    if os.path.exists(categories_path):
        with open(categories_path, "r") as f:
            cats = json.load(f)
        valid_categories = {c["name"] for c in cats.get("categories", [])}

    # Legacy: load categories map from --categories flag (optional)
    categories_map = {}
    if args.categories:
        with open(args.categories, "r") as f:
            categories_map = json.load(f)

    # Load dashboard data
    data = _extract_data_from_html(args.dashboard)
    calls = data.get("calls", [])

    # Build new mentions list from AI features
    new_mentions = []
    keyword_counts = {}
    calls_with_features = set()

    for call in calls:
        call_id = call.get("id", "")
        call_features = features_by_call.get(call_id, [])
        if not call_features:
            continue

        calls_with_features.add(call_id)

        # Build feature request lines for the HubSpot note
        feature_lines = []

        for feat in call_features:
            feature_name = feat.get("feature", "Unknown")
            speaker = feat.get("speaker", "Unknown")
            quote = feat.get("quote", "")
            timestamp = feat.get("timestamp", "")
            ts_seconds = feat.get("ts_seconds")
            feat_type = feat.get("type", "prospect_request")

            # Build deep link
            deep_link = ""
            transcript_url = call.get("transcript", "")
            if ts_seconds is not None and transcript_url:
                base = transcript_url.split("?")[0]
                deep_link = f"{base}?t={int(ts_seconds)}"

            keyword_counts[feature_name] = keyword_counts.get(feature_name, 0) + 1

            # Category: prefer inline from analysis, fallback to map, then "Other"
            category = feat.get("category") or categories_map.get(feature_name, "Other")
            if valid_categories and category not in valid_categories:
                category = "Other"

            mention_id = generate_mention_id(call_id, feature_name)

            new_mentions.append({
                "mention_id": mention_id,
                "call_id": call_id,
                "call_title": call.get("title", ""),
                "call_date": call.get("date", ""),
                "speaker": speaker,
                "company": feat.get("company", ""),
                "contact_title": feat.get("contact_title", ""),
                "keyword": feature_name,
                "category": category,
                "confidence": feat.get("confidence"),
                "type": feat_type,
                "text": quote,
                "ts": timestamp,
                "ts_sec": ts_seconds,
                "link": deep_link,
                "transcript": transcript_url,
            })

            # For HubSpot note
            ts_part = f" ({timestamp})" if timestamp else ""
            short_quote = quote[:120].replace("\n", " ")
            feature_lines.append(f"- {feature_name}{ts_part} - \"{short_quote}\"")

        # Update the HubSpot note embedded in the call data
        override = notes_by_call.get(call_id)
        if override:
            # Use the full CC-generated note as-is
            call["hubspot_note"] = override
        elif feature_lines:
            note = call.get("hubspot_note", "")
            # Insert FEATURE REQUESTS section before the TRANSCRIPT line
            fr_section = "---\nFEATURE REQUESTS\n" + "\n".join(feature_lines)

            if "---\nTRANSCRIPT:" in note:
                note = note.replace("---\nTRANSCRIPT:", fr_section + "\n---\nTRANSCRIPT:")
            elif "FEATURE REQUESTS" not in note:
                # Append at end if no transcript line
                note = note + "\n" + fr_section

            call["hubspot_note"] = note

    # Clear pending_analysis flag on all calls that now have features
    for call in calls:
        call_id = call.get("id", "")
        if call_id in calls_with_features:
            call.pop("pending_analysis", None)

    # Fully rebuild stats from the complete dataset
    data["mentions"] = new_mentions
    data["stats"] = {
        "total_mentions": len(new_mentions),
        "unique_calls": len(calls_with_features),
        "unique_features": len(keyword_counts),
        "generated": data.get("stats", {}).get("generated", ""),
    }
    if recap_text:
        data["recap"] = recap_text
    if company_summaries:
        data["company_summaries"] = company_summaries
    if marketing_report:
        data["marketing_report"] = marketing_report

    # Write updated dashboard
    _write_data_to_html(args.dashboard, data)

    # Write canonical features.json alongside the dashboard
    output_dir = os.path.dirname(args.dashboard)
    write_canonical_json(data, output_dir)

    pending_remaining = sum(1 for c in calls if c.get("pending_analysis"))
    print(f"Updated dashboard: {len(new_mentions)} features across {len(calls_with_features)} calls")
    if pending_remaining:
        print(f"  {pending_remaining} call(s) still pending analysis")

    # Print category distribution
    cat_counts = {}
    for m in new_mentions:
        c = m.get("category", "Other")
        cat_counts[c] = cat_counts.get(c, 0) + 1
    print(f"\n  Category distribution:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")
    other_count = cat_counts.get("Other", 0)
    if other_count:
        print(f"\n  WARNING: {other_count} feature(s) categorized as 'Other'")

    # Optionally regenerate HubSpot notes
    if args.notes:
        with open(args.notes, "w") as f:
            for i, call in enumerate(calls):
                if i > 0:
                    f.write("\n\n" + "=" * 70 + "\n\n")
                f.write(call.get("hubspot_note", ""))
        print(f"Updated HubSpot notes: {args.notes}")

    # Optionally sync to Google Sheets
    if args.sync_sheets:
        try:
            from sync_to_sheets import sync
            result = sync(output_dir=output_dir)
            print(f"Sheet sync: {result['rows_added']} added, {result['rows_updated']} updated")
        except Exception as e:
            print(f"Sheet sync failed: {e}")


def cmd_normalize(args):
    """Normalize feature names in a features JSON file using a merge map."""
    with open(args.features_json, "r") as f:
        features_by_call = json.load(f)

    if args.list_only:
        # Just print unique feature names for review
        all_names = set()
        for call_features in features_by_call.values():
            for feat in call_features:
                all_names.add(feat.get("feature", ""))
        print(f"Unique feature names ({len(all_names)}):\n")
        for name in sorted(all_names):
            print(f"  - {name}")
        return

    if not args.merge_map:
        print("Error: --merge-map is required (or use --list to just view names)")
        sys.exit(1)

    # Load merge map: {"old name": "canonical name"}
    with open(args.merge_map, "r") as f:
        merge_map = json.load(f)

    # Apply merges
    rename_count = 0
    all_names = set()

    for call_id, call_features in features_by_call.items():
        for feat in call_features:
            old_name = feat.get("feature", "")
            if old_name in merge_map:
                feat["feature"] = merge_map[old_name]
                rename_count += 1
            all_names.add(feat["feature"])

    # Write updated features
    with open(args.features_json, "w") as f:
        json.dump(features_by_call, f, indent=2)

    print(f"Normalized {rename_count} feature name(s) across {len(features_by_call)} calls")

    # Print merge summary
    if merge_map:
        print("\nMerges applied:")
        for old, new in sorted(merge_map.items()):
            print(f"  {old}  →  {new}")

    # Update canonical names cache
    existing = _load_canonical_names()
    combined = list(set(existing) | all_names)
    _save_canonical_names(combined)

    print(f"\nFinal feature names ({len(all_names)}):")
    for name in sorted(all_names):
        print(f"  - {name}")


def cmd_refetch_empty(args):
    """Re-fetch transcripts from Fireflies for calls with empty transcript_text."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

    api_key = os.getenv('FIREFLIES_API_KEY')
    if not api_key:
        print("Error: FIREFLIES_API_KEY not set in .env")
        sys.exit(1)

    from client import FirefliesRetriever

    data = _extract_data_from_html(args.dashboard)
    calls = data.get("calls", [])

    # Find calls with empty transcripts
    empty_calls = [c for c in calls if not c.get("transcript_text", "").strip()]

    if not empty_calls:
        print("All calls have transcript text. Nothing to refetch.")
        return

    print(f"Found {len(empty_calls)} call(s) with empty transcripts:\n")
    for c in empty_calls:
        print(f"  - {c.get('title', '?')} ({c.get('date', '?')}) [{c.get('id', '?')}]")
    print()

    retriever = FirefliesRetriever(api_key)
    updated = 0

    for call_data in empty_calls:
        call_id = call_data.get("id", "")
        if not call_id:
            continue

        print(f"Refetching: {call_data.get('title', '?')}...")
        call = retriever.fetch_single_transcript(call_id, verbose=True)

        if call and call.full_transcript_text:
            call_data["transcript_text"] = call.full_transcript_text
            # Also fix duration if it was wrong
            if call.duration > 0:
                call_data["duration"] = round(call.duration / 60) if call.duration > 300 else round(call.duration)
            # Fix attendees if empty
            if not call_data.get("attendees") and call.attendee_names:
                call_data["attendees"] = ", ".join(call.attendee_names)
            updated += 1
            print(f"  -> Got {len(call.full_transcript_text)} chars")
        else:
            print(f"  -> Still empty (transcript may not be processed yet)")

    if updated:
        _write_data_to_html(args.dashboard, data)
        print(f"\nUpdated {updated} call(s) with fresh transcripts.")
    else:
        print("\nNo transcripts were updated.")


def main():
    parser = argparse.ArgumentParser(description="AI feature analysis helper")
    sub = parser.add_subparsers(dest="command")

    # Extract
    p_extract = sub.add_parser("extract", help="Print call transcripts from dashboard")
    p_extract.add_argument("dashboard", help="Path to dashboard HTML file")
    p_extract.add_argument("--titles-only", action="store_true",
                           help="Only print call titles, not full transcripts")
    p_extract.add_argument("--all", action="store_true",
                           help="Extract all calls, not just pending/unanalyzed")
    p_extract.add_argument("--prior", metavar="DASHBOARD",
                           help="Load existing feature names from a prior dashboard")

    # Normalize
    p_norm = sub.add_parser("normalize", help="Normalize feature names via merge map")
    p_norm.add_argument("features_json", help="Path to features JSON file")
    p_norm.add_argument("--merge-map", metavar="FILE",
                        help="JSON file mapping old names to canonical names")
    p_norm.add_argument("--list", dest="list_only", action="store_true",
                        help="Just list unique feature names (no changes)")

    # Inject
    p_inject = sub.add_parser("inject", help="Inject AI features into dashboard")
    p_inject.add_argument("dashboard", help="Path to dashboard HTML file")
    p_inject.add_argument("features_json", help="Path to features JSON file")
    p_inject.add_argument("--notes", help="Path to HubSpot notes file to update")
    p_inject.add_argument("--categories", metavar="FILE",
                          help="JSON file mapping feature names to category names")
    p_inject.add_argument("--sync-sheets", action="store_true",
                          help="Sync to Google Sheet after injecting features")

    # Refetch empty transcripts
    p_refetch = sub.add_parser("refetch-empty",
                               help="Re-pull transcripts from Fireflies for calls with empty transcript_text")
    p_refetch.add_argument("dashboard", help="Path to dashboard HTML file")

    args = parser.parse_args()
    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "normalize":
        cmd_normalize(args)
    elif args.command == "inject":
        cmd_inject(args)
    elif args.command == "refetch-empty":
        cmd_refetch_empty(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
