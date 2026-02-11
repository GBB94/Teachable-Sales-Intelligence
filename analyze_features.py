#!/usr/bin/env python3
"""
AI feature analysis helper for Claude Code workflow.

Two-step process:
  1. `extract` — reads a dashboard HTML file, prints call transcripts for review
  2. `inject`  — takes a features JSON file, rewrites the dashboard + HubSpot notes

Usage (inside Claude Code):
  python3 analyze_features.py extract test_output/dashboard.html
  # ... Claude Code reads transcripts, builds features.json ...
  python3 analyze_features.py inject test_output/dashboard.html features.json [--notes test_output/notes.txt]
"""

import argparse
import json
import os
import re
import sys


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
    # Load features JSON
    with open(args.features_json, "r") as f:
        features_by_call = json.load(f)

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
        if feature_lines:
            note = call.get("hubspot_note", "")
            # Insert FEATURE REQUESTS section before the TRANSCRIPT line
            fr_section = "---\nFEATURE REQUESTS\n" + "\n".join(feature_lines)

            if "---\nTRANSCRIPT:" in note:
                note = note.replace("---\nTRANSCRIPT:", fr_section + "\n---\nTRANSCRIPT:")
            elif "FEATURE REQUESTS" not in note:
                # Append at end if no transcript line
                note = note + "\n" + fr_section

            call["hubspot_note"] = note

    # Update stats and mentions in dashboard data
    data["mentions"] = new_mentions
    data["stats"] = {
        "total_mentions": len(new_mentions),
        "unique_calls": len(calls_with_features),
        "unique_features": len(keyword_counts),
        "generated": data.get("stats", {}).get("generated", ""),
    }

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


def main():
    parser = argparse.ArgumentParser(description="AI feature analysis helper")
    sub = parser.add_subparsers(dest="command")

    # Extract
    p_extract = sub.add_parser("extract", help="Print call transcripts from dashboard")
    p_extract.add_argument("dashboard", help="Path to dashboard HTML file")
    p_extract.add_argument("--titles-only", action="store_true",
                           help="Only print call titles, not full transcripts")

    # Inject
    p_inject = sub.add_parser("inject", help="Inject AI features into dashboard")
    p_inject.add_argument("dashboard", help="Path to dashboard HTML file")
    p_inject.add_argument("features_json", help="Path to features JSON file")
    p_inject.add_argument("--notes", help="Path to HubSpot notes file to update")

    args = parser.parse_args()
    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "inject":
        cmd_inject(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
