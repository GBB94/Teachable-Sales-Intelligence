#!/usr/bin/env python3
"""
CLI for Fireflies call retrieval, HubSpot note generation, and feature request tracking.
"""

import argparse
import os

from dotenv import load_dotenv

from client import FirefliesRetriever
from models import CallFilter
from exports import (
    export_to_json,
    export_to_csv,
    export_hubspot_notes,
    export_feature_report,
    export_feature_report_csv,
    export_feature_dashboard,
)


def _output_path(output_dir: str, filename: str) -> str:
    """Prefix filename with output_dir, creating the directory if needed."""
    if not output_dir:
        return filename
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, filename)


def main():
    parser = argparse.ArgumentParser(
        description='Retrieve and filter Fireflies calls',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get all calls from last 30 days
  %(prog)s --days 30

  # Get calls owned by specific user(s)
  %(prog)s --owner zach@teachable.com --days 60

  # Get sales calls with keywords
  %(prog)s --title-keywords demo discovery sales --days 30

  # Generate HubSpot-ready notes
  %(prog)s --days 30 --hubspot-notes sales_notes.txt

  # Scan for feature requests
  %(prog)s --days 60 --feature-requests

  # Full pipeline with organized output
  %(prog)s --owner zach@teachable.com --days 90 \\
    --export q1_calls --hubspot-notes q1_notes.txt \\
    --feature-requests --dashboard features.html \\
    --output-dir output/

  # Discover users
  %(prog)s --list-users
        """
    )

    # Filter options
    parser.add_argument('--days', type=int, help='Days to look back (default: 14)')
    parser.add_argument('--limit', type=int, default=100, help='Max calls to retrieve')

    parser.add_argument('--owner', '--owner-email', nargs='+',
                        help='Filter by organizer email(s) (partial match)')
    parser.add_argument('--attendee', '--attendee-email', nargs='+',
                        help='Filter by attendee email(s) (partial match)')

    parser.add_argument('--title-keywords', nargs='+',
                        help='Keywords to find in title (OR logic)')
    parser.add_argument('--transcript-keywords', nargs='+',
                        help='Keywords to find in transcript (OR logic)')

    parser.add_argument('--min-duration', type=int,
                        help='Minimum duration in seconds')
    parser.add_argument('--max-duration', type=int,
                        help='Maximum duration in seconds')

    # Actions
    parser.add_argument('--list-users', action='store_true',
                        help='List all users found in recent calls')
    parser.add_argument('--export', metavar='FILENAME',
                        help='Export results to JSON and CSV files')
    parser.add_argument('--hubspot-notes', metavar='FILENAME',
                        help='Export HubSpot-ready call notes to a text file')
    parser.add_argument('--feature-requests', action='store_true',
                        help='Scan transcripts for feature request mentions')
    parser.add_argument('--feature-export', metavar='FILENAME',
                        help='Export feature request report to JSON + CSV (implies --feature-requests)')
    parser.add_argument('--exclude-domains', nargs='+', default=None,
                        help='Email domains to exclude from feature scanning (default: teachable.com)')
    parser.add_argument('--exclude-speakers', nargs='+', default=None,
                        help='Speaker names to exclude from feature scanning (default: "zach mccall")')
    parser.add_argument('--dashboard', metavar='FILENAME',
                        help='Generate an interactive HTML feature dashboard (implies --feature-requests)')
    parser.add_argument('--output-dir', metavar='DIR', default='',
                        help='Directory for all output files (created if needed)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Fetch one batch, show filter match rate, estimate API calls needed, then stop')
    parser.add_argument('--batch-delay', type=float, default=1.0,
                        help='Delay in seconds between API batches (default: 1.0)')
    parser.add_argument('--backfill', action='store_true',
                        help='Backfill mode: scan 90 days with limit 200 (more API calls than normal)')

    args = parser.parse_args()

    # Load .env file (if present)
    load_dotenv()

    # Get API key
    api_key = os.getenv('FIREFLIES_API_KEY')
    if not api_key:
        print("Error: FIREFLIES_API_KEY not set")
        print("\nTo fix this, either:")
        print("  1. Create a .env file:  echo 'FIREFLIES_API_KEY=your_key' > .env")
        print("  2. Export in shell:     export FIREFLIES_API_KEY='your_key'")
        return 1

    retriever = FirefliesRetriever(api_key, request_delay=args.batch_delay)

    # ------------------------------------------------------------------
    # List users mode
    # ------------------------------------------------------------------
    if args.list_users:
        print("Discovering users from recent calls...\n")
        users = retriever.get_user_list(limit=100)

        print(f"Found {len(users)} unique users:\n")
        for user in sorted(users, key=lambda x: x['name']):
            print(f"  {user['name']:30s} {user['email']}")

        return 0

    # ------------------------------------------------------------------
    # Backfill mode
    # ------------------------------------------------------------------
    if args.backfill:
        print("WARNING: Backfill mode — scanning 90 days, this will make more API calls than a normal run.\n")
        if not args.days:
            args.days = 90
        if args.limit == 100:  # unchanged from default
            args.limit = 200

    # ------------------------------------------------------------------
    # Build filter
    # ------------------------------------------------------------------
    filter_kwargs = {}

    if args.days:
        filter_kwargs['days_back'] = args.days
    if args.limit:
        filter_kwargs['limit'] = args.limit
    if args.owner:
        filter_kwargs['owner_emails'] = args.owner
    if args.attendee:
        filter_kwargs['attendee_emails'] = args.attendee
    if args.title_keywords:
        filter_kwargs['title_keywords'] = args.title_keywords
    if args.transcript_keywords:
        filter_kwargs['transcript_keywords'] = args.transcript_keywords
    if args.min_duration is not None:
        filter_kwargs['min_duration'] = args.min_duration
    if args.max_duration is not None:
        filter_kwargs['max_duration'] = args.max_duration

    # ------------------------------------------------------------------
    # Dry run mode
    # ------------------------------------------------------------------
    filter_criteria = CallFilter(**filter_kwargs)

    if args.dry_run:
        print("DRY RUN: Fetching one batch to estimate API usage...\n")
        batch = retriever.fetch_raw_transcripts(limit=50, skip=0)
        if not batch:
            print("   No transcripts returned from API.")
            return 0
        matches = sum(1 for raw in batch if retriever._matches_filter(raw, filter_criteria))
        match_rate = matches / len(batch) if batch else 0
        print(f"   Batch size: {len(batch)} raw calls")
        print(f"   Matches current filters: {matches}/{len(batch)} ({match_rate:.0%})")
        if match_rate > 0:
            estimated_batches = int((filter_criteria.limit / match_rate) / 50) + 1
        else:
            estimated_batches = "unknown (0% match rate)"
        print(f"   Estimated API calls to fill limit of {filter_criteria.limit}: {estimated_batches}")
        print(f"\n   API calls made: {retriever.api_calls_made} ({retriever.raw_transcripts_fetched} raw transcripts fetched)")
        return 0

    # ------------------------------------------------------------------
    # Get calls
    # ------------------------------------------------------------------
    calls = retriever.get_calls(filter_criteria=filter_criteria)

    # Display results
    print("=" * 80)
    print(f"RESULTS: {len(calls)} calls found")
    print("=" * 80)
    print()

    for i, call in enumerate(calls, 1):
        print(f"{i}. {call.title}")
        print(f"   Date: {call.date}")
        print(f"   Duration: {call.duration_minutes:.1f} minutes")
        print(f"   Organizer: {call.organizer_email or 'Unknown'}")
        print(f"   Attendees: {', '.join(call.attendee_names) or 'None'}")

        if call.summary and call.summary.get('overview'):
            overview = call.summary['overview']
            if len(overview) > 150:
                overview = overview[:150] + "..."
            print(f"   Summary: {overview}")

        print()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    od = args.output_dir

    if args.export and calls:
        export_to_json(calls, _output_path(od, f"{args.export}.json"))
        export_to_csv(calls, _output_path(od, f"{args.export}.csv"))

    # ------------------------------------------------------------------
    # HubSpot notes
    # ------------------------------------------------------------------
    if args.hubspot_notes and calls:
        path = _output_path(od, args.hubspot_notes)
        export_hubspot_notes(calls, path)
        print(f"\n   Open {path} and copy/paste notes into HubSpot.\n")

    # ------------------------------------------------------------------
    # Feature requests
    # ------------------------------------------------------------------
    if args.feature_requests or args.feature_export or args.dashboard:
        scan_kwargs = {}
        if args.exclude_domains is not None:
            scan_kwargs['exclude_domains'] = args.exclude_domains
        if args.exclude_speakers is not None:
            scan_kwargs['exclude_speakers'] = args.exclude_speakers

        report = retriever.scan_feature_requests(calls, **scan_kwargs)

        if args.feature_export:
            export_feature_report(report, _output_path(od, f"{args.feature_export}.json"))
            export_feature_report_csv(report, _output_path(od, f"{args.feature_export}.csv"))

        if args.dashboard:
            path = _output_path(od, args.dashboard)
            export_feature_dashboard(report, calls, path)
            print(f"\n   Open {path} in your browser to review.\n")

    # ------------------------------------------------------------------
    # API usage summary
    # ------------------------------------------------------------------
    print(f"API calls made: {retriever.api_calls_made} ({retriever.raw_transcripts_fetched} raw transcripts fetched)")

    return 0


if __name__ == "__main__":
    exit(main())
