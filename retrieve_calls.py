#!/usr/bin/env python3
"""
CLI for Fireflies call retrieval, HubSpot note generation, and feature request tracking.
"""

import argparse
import os
from fireflies_retriever import FirefliesRetriever, CallFilter


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
  %(prog)s --owner zach@teachable.com jane@teachable.com --days 60

  # Get sales calls with keywords
  %(prog)s --title-keywords demo discovery sales --days 30

  # Complex filter
  %(prog)s --owner zach@teachable.com --title-keywords demo --min-duration 600 --days 90

  # Generate HubSpot-ready notes
  %(prog)s --days 30 --hubspot-notes sales_notes.txt

  # Scan for feature requests
  %(prog)s --days 60 --feature-requests

  # Full pipeline: filter, export, generate notes, scan features
  %(prog)s --owner zach@teachable.com --days 90 --export q1_calls --hubspot-notes q1_notes.txt --feature-requests

  # Discover users
  %(prog)s --list-users
        """
    )

    # Filter options
    parser.add_argument('--days', type=int, help='Days to look back (default: 30)')
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

    args = parser.parse_args()

    # Get API key
    api_key = os.getenv('FIREFLIES_API_KEY')
    if not api_key:
        print("Error: FIREFLIES_API_KEY environment variable not set")
        print("\nTo fix this:")
        print("  export FIREFLIES_API_KEY='your_api_key_here'")
        return 1

    retriever = FirefliesRetriever(api_key)

    # ------------------------------------------------------------------
    # List users mode
    # ------------------------------------------------------------------
    if args.list_users:
        print("🔍 Discovering users from recent calls...\n")
        users = retriever.get_user_list(limit=100)

        print(f"Found {len(users)} unique users:\n")
        for user in sorted(users, key=lambda x: x['name']):
            print(f"  {user['name']:30s} {user['email']}")

        return 0

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
    # Get calls
    # ------------------------------------------------------------------
    filter_criteria = CallFilter(**filter_kwargs)
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
    if args.export and calls:
        retriever.export_to_json(calls, f"{args.export}.json")
        retriever.export_to_csv(calls, f"{args.export}.csv")

    # ------------------------------------------------------------------
    # HubSpot notes
    # ------------------------------------------------------------------
    if args.hubspot_notes and calls:
        retriever.export_hubspot_notes(calls, args.hubspot_notes)
        print(f"\n   Open {args.hubspot_notes} and copy/paste notes into HubSpot.\n")

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
            retriever.export_feature_report(report, f"{args.feature_export}.json")
            retriever.export_feature_report_csv(report, f"{args.feature_export}.csv")

        if args.dashboard:
            retriever.export_feature_dashboard(report, calls, args.dashboard)
            print(f"\n   Open {args.dashboard} in your browser to review.\n")

    return 0


if __name__ == "__main__":
    exit(main())
