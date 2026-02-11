"""
Export helpers: JSON, CSV, HubSpot notes, feature reports, and HTML dashboard.
"""

import json
import csv
import os
from typing import List
from string import Template

from models import Call, FeatureRequestReport


DASHBOARD_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "dashboard_template.html")


def export_to_json(calls: List[Call], filename: str):
    data = [call.to_dict() for call in calls]
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Exported {len(calls)} calls to {filename}")


def export_to_csv(calls: List[Call], filename: str):
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ID", "Title", "Date", "Duration (min)",
            "Organizer", "Attendees", "Transcript URL",
        ])
        for call in calls:
            writer.writerow([
                call.id, call.title, call.date,
                f"{call.duration_minutes:.1f}",
                call.organizer_email or "",
                "; ".join(call.attendee_names),
                call.transcript_url or "",
            ])
    print(f"Exported {len(calls)} calls to {filename}")


def export_hubspot_notes(calls: List[Call], filename: str):
    with open(filename, "w") as f:
        for i, call in enumerate(calls):
            if i > 0:
                f.write("\n\n" + "=" * 70 + "\n\n")
            f.write(call.to_hubspot_note())
    print(f"Exported {len(calls)} HubSpot notes to {filename}")


def export_feature_report(report: FeatureRequestReport, filename: str):
    with open(filename, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(f"Exported feature request report to {filename}")


def export_feature_report_csv(report: FeatureRequestReport, filename: str):
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Call Date", "Call Title", "Speaker", "Timestamp",
            "Keyword Matched", "Context", "Deep Link", "Attendee Emails",
        ])
        for req in report.requests:
            writer.writerow([
                req.call_date[:10] if req.call_date else "",
                req.call_title,
                req.speaker,
                req.timestamp_display,
                req.keyword_matched,
                req.surrounding_text.replace("\n", " "),
                req.deep_link or "",
                "; ".join(req.attendee_emails),
            ])
    print(f"Exported {len(report.requests)} feature mentions to {filename}")


def export_feature_dashboard(
    report: FeatureRequestReport,
    calls: List[Call],
    filename: str,
):
    """Generate an interactive HTML dashboard from a template file."""
    mentions_data = []
    for req in report.requests:
        mentions_data.append({
            "call_id": req.call_id,
            "call_title": req.call_title,
            "call_date": req.call_date[:10] if req.call_date else "",
            "speaker": req.speaker,
            "keyword": req.keyword_matched,
            "text": req.surrounding_text[:200].replace("\n", " "),
            "ts": req.timestamp_display,
            "ts_sec": req.timestamp_seconds,
            "link": req.deep_link or "",
            "transcript": req.transcript_url or "",
        })

    calls_data = []
    for call in calls:
        calls_data.append({
            "id": call.id,
            "title": call.title,
            "date": call.date[:10] if call.date else "",
            "duration": round(call.duration_minutes),
            "organizer": call.organizer_email or "",
            "attendees": ", ".join(call.attendee_names) or "",
            "transcript": call.transcript_url or "",
            "hubspot_note": call.to_hubspot_note(),
            "transcript_text": call.full_transcript_text or "",
        })

    stats = {
        "total_mentions": report.total_mentions,
        "unique_calls": report.unique_calls,
        "unique_features": len(report.keyword_counts),
        "generated": report.generated_at[:10],
    }

    data_json = json.dumps({
        "stats": stats,
        "mentions": mentions_data,
        "calls": calls_data,
    })

    with open(DASHBOARD_TEMPLATE_PATH, "r") as f:
        template = f.read()

    html = template.replace("{{DATA_JSON}}", data_json)

    with open(filename, "w") as f:
        f.write(html)
    print(f"Exported feature dashboard to {filename}")
