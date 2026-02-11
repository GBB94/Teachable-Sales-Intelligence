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
import json
import os
import re
import sys


CANONICAL_NAMES_FILE = ".feature_names"


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
    """Extract and print call transcripts from dashboard HTML."""
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
    calls = data.get("calls", [])

    print(f"Found {len(calls)} calls in dashboard.\n")

    for i, call in enumerate(calls, 1):
        transcript = call.get("transcript_text", "")
        word_count = len(transcript.split()) if transcript else 0

        print(f"{'='*70}")
        print(f"[{i}] {call['title']}")
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
    else:
        features_by_call = raw
        notes_by_call = {}
        recap_text = ""
        company_summaries = {}

    # Load categories map (optional)
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

            # Build deep link
            deep_link = ""
            transcript_url = call.get("transcript", "")
            if ts_seconds is not None and transcript_url:
                base = transcript_url.split("?")[0]
                deep_link = f"{base}?t={int(ts_seconds)}"

            keyword_counts[feature_name] = keyword_counts.get(feature_name, 0) + 1

            new_mentions.append({
                "call_id": call_id,
                "call_title": call.get("title", ""),
                "call_date": call.get("date", ""),
                "speaker": speaker,
                "keyword": feature_name,
                "category": categories_map.get(feature_name, "Other"),
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

    # Update stats, mentions, and recap in dashboard data
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

    # Write updated dashboard
    _write_data_to_html(args.dashboard, data)
    print(f"Updated dashboard: {len(new_mentions)} features across {len(calls_with_features)} calls")

    # Optionally regenerate HubSpot notes
    if args.notes:
        with open(args.notes, "w") as f:
            for i, call in enumerate(calls):
                if i > 0:
                    f.write("\n\n" + "=" * 70 + "\n\n")
                f.write(call.get("hubspot_note", ""))
        print(f"Updated HubSpot notes: {args.notes}")


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


def main():
    parser = argparse.ArgumentParser(description="AI feature analysis helper")
    sub = parser.add_subparsers(dest="command")

    # Extract
    p_extract = sub.add_parser("extract", help="Print call transcripts from dashboard")
    p_extract.add_argument("dashboard", help="Path to dashboard HTML file")
    p_extract.add_argument("--titles-only", action="store_true",
                           help="Only print call titles, not full transcripts")
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

    args = parser.parse_args()
    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "normalize":
        cmd_normalize(args)
    elif args.command == "inject":
        cmd_inject(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
