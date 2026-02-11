#!/usr/bin/env python3
"""
Example usage patterns for Fireflies Retriever.
Covers filtering, HubSpot notes, and feature request tracking.
"""

import os
from fireflies_retriever import FirefliesRetriever, CallFilter


def print_section(title):
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80 + "\n")


def print_calls(calls, max_display=5):
    if not calls:
        print("   No calls found.")
        return

    for i, call in enumerate(calls[:max_display], 1):
        print(f"{i}. {call.title}")
        print(f"   📅 {call.date}")
        print(f"   ⏱️  {call.duration_minutes:.1f} minutes")
        print(f"   👤 {call.organizer_email or 'Unknown organizer'}")
        print(f"   👥 {', '.join(call.attendee_names[:3]) or 'No attendees'}")
        print()

    if len(calls) > max_display:
        print(f"   ... and {len(calls) - max_display} more calls")
        print()


def main():
    api_key = os.getenv('FIREFLIES_API_KEY')

    if not api_key:
        print("Please set FIREFLIES_API_KEY environment variable")
        print("\n   export FIREFLIES_API_KEY='your_api_key_here'")
        return

    retriever = FirefliesRetriever(api_key)

    # ----- 1. Basic retrieval -----
    print_section("1. All Calls from Last 30 Days")
    calls = retriever.get_calls(
        filter_criteria=CallFilter(days_back=30, limit=10),
        verbose=True
    )
    print_calls(calls)

    # ----- 2. Discover users -----
    print_section("2. Discover Users in Your Account")
    users = retriever.get_user_list(limit=50)
    print(f"Found {len(users)} unique users:\n")
    for user in users[:10]:
        print(f"   {user['name']:30s} {user['email']}")
    if len(users) > 10:
        print(f"\n   ... and {len(users) - 10} more users")
    print()

    # ----- 3. Filter by owner -----
    print_section("3. Calls You Organized")
    your_email = input("Enter your email (or press Enter to skip): ").strip()

    if your_email:
        calls = retriever.get_calls(
            filter_criteria=CallFilter(
                days_back=60,
                owner_emails=[your_email],
                limit=10
            ),
            verbose=True
        )
        print_calls(calls)
    else:
        print("   Skipped.\n")

    # ----- 4. Sales/demo calls -----
    print_section("4. Sales & Demo Calls (Keywords in Title)")
    calls = retriever.get_calls(
        filter_criteria=CallFilter(
            days_back=60,
            title_keywords=["demo", "discovery", "sales", "call"],
            limit=10
        ),
        verbose=True
    )
    print_calls(calls)

    # ----- 5. Long calls -----
    print_section("5. Long Calls (30+ minutes)")
    calls = retriever.get_calls(
        filter_criteria=CallFilter(
            days_back=30,
            min_duration=1800,
            limit=10
        ),
        verbose=True
    )
    print_calls(calls)

    # ----- 6. Transcript keyword search -----
    print_section("6. Calls Mentioning 'Continuing Education' or 'Certification'")
    print("Note: Searches full transcripts, may take longer...\n")
    calls = retriever.get_calls(
        filter_criteria=CallFilter(
            days_back=60,
            transcript_keywords=["continuing education", "certification", "credits"],
            limit=5
        ),
        verbose=True
    )
    print_calls(calls)

    # ----- 7. HubSpot notes -----
    print_section("7. HubSpot-Ready Call Notes")
    if calls:
        print("Here's what a HubSpot note looks like for the first call:\n")
        print(calls[0].to_hubspot_note())
        print()

        export = input("Export all HubSpot notes to file? (y/n): ").strip().lower()
        if export == 'y':
            retriever.export_hubspot_notes(calls, "hubspot_notes.txt")
            print("\n   Open hubspot_notes.txt and copy/paste into HubSpot.\n")
    else:
        print("   No calls to generate notes for.\n")

    # ----- 8. Feature request tracking -----
    print_section("8. Feature Request Scan")
    print("Scanning all retrieved calls for feature request mentions...\n")

    # Re-fetch a broader set for feature scanning
    all_calls = retriever.get_calls(
        filter_criteria=CallFilter(days_back=90, limit=50),
        verbose=True
    )

    if all_calls:
        report = retriever.scan_feature_requests(all_calls)

        export = input("Export feature request report? (y/n): ").strip().lower()
        if export == 'y':
            retriever.export_feature_report(report, "feature_requests.json")
            retriever.export_feature_report_csv(report, "feature_requests.csv")
            print("\n   Created feature_requests.json and feature_requests.csv\n")
    else:
        print("   No calls to scan.\n")

    # ----- Done -----
    print_section("Examples Complete!")
    print("Tips:")
    print("  - Use retrieve_calls.py CLI for quick filtering")
    print("  - --hubspot-notes <file> generates copy/paste-ready call notes")
    print("  - --feature-requests scans transcripts for feature mentions")
    print("  - --feature-export <file> saves the report to JSON + CSV")
    print()


if __name__ == "__main__":
    main()
