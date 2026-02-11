#!/usr/bin/env python3
"""
Fireflies Call Retrieval Layer
Robust filtering by keywords, ownership, date ranges, and metadata.
Includes HubSpot-ready call notes and feature request tracking.
"""

import requests
import json
import csv
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from collections import Counter
import time


# ---------------------------------------------------------------------------
# Default feature request keywords to scan for in transcripts.
# Add/remove terms here or override per-call via scan_feature_requests().
# ---------------------------------------------------------------------------
DEFAULT_FEATURE_KEYWORDS: List[str] = [
    # General product requests
    "feature request",
    "would be nice if",
    "it would be great if",
    "can you add",
    "do you support",
    "is there a way to",
    "we really need",
    "we need the ability",
    "missing feature",
    "wish list",
    "wishlist",
    "on the roadmap",
    "any plans to",
    "are you planning",
    # Continuing-ed / credentialing specific
    "CE credits",
    "continuing education credits",
    "accreditation",
    "certificate template",
    "certificate automation",
    "compliance reporting",
    "credit tracking",
    "license renewal",
    "recertification",
    "SCORM",
    "LTI integration",
    "SSO",
    "single sign-on",
    "API access",
    "bulk upload",
    "white label",
    "custom domain",
    "proctoring",
    "assessment engine",
    "quiz branching",
    "reporting dashboard",
]


@dataclass
class CallFilter:
    """Filter criteria for retrieving calls"""
    # Date filtering
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    days_back: Optional[int] = 30

    # Ownership filtering
    owner_emails: Optional[List[str]] = None
    attendee_emails: Optional[List[str]] = None

    # Keyword filtering
    title_keywords: Optional[List[str]] = None
    transcript_keywords: Optional[List[str]] = None

    # Metadata filtering
    min_duration: Optional[int] = None  # seconds
    max_duration: Optional[int] = None  # seconds

    # Pagination
    limit: int = 100
    skip: int = 0

    def __post_init__(self):
        """Calculate date range if days_back is provided"""
        if self.days_back and not self.start_date:
            self.end_date = datetime.now(timezone.utc)
            self.start_date = self.end_date - timedelta(days=self.days_back)


@dataclass
class FeatureRequest:
    """A single feature request mention found in a transcript"""
    call_id: str
    call_title: str
    call_date: str
    speaker: str
    keyword_matched: str
    surrounding_text: str
    timestamp_seconds: Optional[float] = None  # start_time of the sentence
    timestamp_display: str = ""                 # formatted as MM:SS
    transcript_url: Optional[str] = None        # base transcript URL
    deep_link: Optional[str] = None             # transcript URL with ?t= param
    attendee_emails: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureRequestReport:
    """Aggregated feature request data across calls"""
    total_mentions: int
    unique_calls: int
    keyword_counts: Dict[str, int]
    requests: List[FeatureRequest]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_mentions": self.total_mentions,
            "unique_calls": self.unique_calls,
            "keyword_counts": dict(self.keyword_counts),
            "generated_at": self.generated_at,
            "requests": [r.to_dict() for r in self.requests],
        }

    def _group_by_keyword(self) -> Dict[str, List["FeatureRequest"]]:
        """Group requests by keyword, sorted by count descending."""
        groups: Dict[str, List["FeatureRequest"]] = {}
        for req in self.requests:
            groups.setdefault(req.keyword_matched, []).append(req)
        # Sort groups by count descending
        return dict(sorted(groups.items(), key=lambda x: -len(x[1])))

    def print_summary(self, expand: bool = True, max_mentions_per_keyword: int = 5):
        """
        Print feature request report.

        Args:
            expand: If True, show individual mentions nested under each keyword.
            max_mentions_per_keyword: Max mentions to show per keyword when expanded.
        """
        print(f"\n{'='*70}")
        print(f" FEATURE REQUEST REPORT")
        print(f"{'='*70}")
        print(f"  Total mentions:  {self.total_mentions}")
        print(f"  Across calls:    {self.unique_calls}")
        print(f"  Generated:       {self.generated_at}")
        print()

        grouped = self._group_by_keyword()

        if not grouped:
            print("  No feature request mentions found.")
            return

        for kw, mentions in grouped.items():
            bar = "█" * min(len(mentions), 30)
            print(f"  {len(mentions):>3}x  {kw:<35s} {bar}")

            if expand:
                # Sort mentions by date descending
                sorted_mentions = sorted(
                    mentions,
                    key=lambda r: r.call_date or "",
                    reverse=True
                )
                for req in sorted_mentions[:max_mentions_per_keyword]:
                    date_str = req.call_date[:10] if req.call_date else "N/A"
                    ts = f" @ {req.timestamp_display}" if req.timestamp_display else ""
                    snippet = req.surrounding_text[:100].replace("\n", " ")
                    print(f"        [{date_str}{ts}] {req.call_title}")
                    print(f"          {req.speaker}: \"{snippet}...\"")
                    if req.deep_link:
                        print(f"          -> {req.deep_link}")

                remaining = len(mentions) - max_mentions_per_keyword
                if remaining > 0:
                    print(f"          ... +{remaining} more mentions")

            print()


@dataclass
class Call:
    """Structured representation of a Fireflies call"""
    id: str
    title: str
    date: str
    duration: int  # seconds
    organizer_email: Optional[str]
    attendees: List[Dict[str, str]]
    transcript_url: Optional[str]
    recording_url: Optional[str]
    summary: Optional[Dict[str, Any]]
    sentences: Optional[List[Dict[str, Any]]]
    full_transcript_text: Optional[str] = None

    @property
    def duration_minutes(self) -> float:
        return self.duration / 60

    @property
    def attendee_emails(self) -> List[str]:
        return [a.get('email', '') for a in self.attendees if a.get('email')]

    @property
    def attendee_names(self) -> List[str]:
        return [a.get('displayName', '') for a in self.attendees if a.get('displayName')]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_hubspot_note(self, include_action_items: bool = True) -> str:
        """
        Generate a copy/paste-ready HubSpot call note.

        Format:
          CALL: <title>
          DATE: <date>  |  DURATION: <mins> min
          ORGANIZER: <email>
          ATTENDEES: <names (emails)>
          ---
          SUMMARY
          <overview text>
          ---
          ACTION ITEMS
          - item 1
          - item 2
          ---
          KEY TOPICS
          keyword1, keyword2
          ---
          TRANSCRIPT: <url>
        """
        lines: List[str] = []

        # Header
        lines.append(f"CALL: {self.title}")
        date_short = self.date[:10] if self.date else "N/A"
        lines.append(f"DATE: {date_short}  |  DURATION: {self.duration_minutes:.0f} min")
        lines.append(f"ORGANIZER: {self.organizer_email or 'Unknown'}")

        attendee_parts = []
        for a in self.attendees:
            name = a.get('displayName', '')
            email = a.get('email', '')
            if name and email:
                attendee_parts.append(f"{name} ({email})")
            elif email:
                attendee_parts.append(email)
            elif name:
                attendee_parts.append(name)
        lines.append(f"ATTENDEES: {', '.join(attendee_parts) or 'None listed'}")

        lines.append("---")

        # Summary
        overview = ""
        if self.summary:
            overview = self.summary.get('overview', '') or ''
        lines.append("SUMMARY")
        lines.append(overview.strip() if overview.strip() else "(No summary available)")

        # Action items
        if include_action_items and self.summary:
            action_items = self.summary.get('action_items') or []
            if action_items:
                lines.append("---")
                lines.append("ACTION ITEMS")
                for item in action_items:
                    if isinstance(item, str):
                        lines.append(f"- {item}")
                    elif isinstance(item, dict):
                        lines.append(f"- {item.get('text', str(item))}")

        # Keywords / topics
        if self.summary:
            keywords = self.summary.get('keywords') or []
            if keywords:
                lines.append("---")
                lines.append("KEY TOPICS")
                lines.append(", ".join(keywords))

        # Transcript link
        if self.transcript_url:
            lines.append("---")
            lines.append(f"TRANSCRIPT: {self.transcript_url}")

        return "\n".join(lines)


class FirefliesRetriever:
    """Robust layer for retrieving and filtering Fireflies calls."""

    MAX_RETRIES = 2
    RETRY_BACKOFF = 1.0  # seconds; doubles each retry

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.fireflies.ai/graphql"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _make_request(self, query: str, variables: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make a GraphQL request with retry + error handling."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = requests.post(
                    self.base_url,
                    json=payload,
                    headers=self.headers,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                if 'errors' in data:
                    raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
                return data

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    wait = self.RETRY_BACKOFF * (2 ** (attempt - 1))
                    print(f"   Request failed (attempt {attempt}/{self.MAX_RETRIES}), retrying in {wait:.1f}s...")
                    time.sleep(wait)

        raise Exception(f"Request failed after {self.MAX_RETRIES} attempts: {last_error}")

    def fetch_raw_transcripts(self, limit: int = 100, skip: int = 0) -> List[Dict[str, Any]]:
        """Fetch raw transcripts from Fireflies API."""
        query = """
        query GetTranscripts($limit: Int!, $skip: Int!) {
          transcripts(limit: $limit, skip: $skip) {
            id
            title
            date
            duration
            organizer_email
            transcript_url
            video_url
            meeting_attendees {
              displayName
              email
              name
            }
            sentences {
              text
              speaker_name
              speaker_id
              start_time
              end_time
            }
            summary {
              overview
              shorthand_bullet
              keywords
              action_items
              outline
            }
          }
        }
        """
        variables = {"limit": limit, "skip": skip}
        try:
            data = self._make_request(query, variables)
            return data.get('data', {}).get('transcripts', [])
        except Exception as e:
            print(f"Error fetching transcripts: {e}")
            return []

    def _build_full_transcript(self, sentences: List[Dict[str, Any]]) -> str:
        """Build full transcript text from sentences."""
        if not sentences:
            return ""
        return "\n".join([
            f"{s.get('speaker_name', 'Unknown')}: {s.get('text', '')}"
            for s in sentences
        ])

    def _parse_call_date(self, date_str: str) -> Optional[datetime]:
        """Safely parse a call date string into a timezone-aware datetime."""
        if not date_str:
            return None
        try:
            cleaned = date_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            return None

    def _matches_filter(self, raw_call: Dict[str, Any], filter_criteria: CallFilter) -> bool:
        """Check if a call matches filter criteria. True = passes all filters."""

        # Date filtering
        if filter_criteria.start_date or filter_criteria.end_date:
            call_date = self._parse_call_date(raw_call.get('date', ''))
            if call_date is None:
                return False
            if filter_criteria.start_date and call_date < filter_criteria.start_date:
                return False
            if filter_criteria.end_date and call_date > filter_criteria.end_date:
                return False

        # Duration filtering (is not None to allow 0)
        duration = raw_call.get('duration', 0)
        if filter_criteria.min_duration is not None and duration < filter_criteria.min_duration:
            return False
        if filter_criteria.max_duration is not None and duration > filter_criteria.max_duration:
            return False

        # Owner email filtering
        if filter_criteria.owner_emails:
            organizer = raw_call.get('organizer_email', '').lower()
            if not any(owner.lower() in organizer for owner in filter_criteria.owner_emails):
                return False

        # Attendee filtering
        if filter_criteria.attendee_emails:
            attendee_emails = [
                a.get('email', '').lower()
                for a in raw_call.get('meeting_attendees', [])
            ]
            if not any(
                any(fe.lower() in ae for ae in attendee_emails)
                for fe in filter_criteria.attendee_emails
            ):
                return False

        # Title keyword filtering (normalizes hyphens/spaces for flexible matching)
        if filter_criteria.title_keywords:
            title = raw_call.get('title', '').lower()
            title_normalized = title.replace('-', ' ').replace('_', ' ')
            if not any(
                kw.lower() in title or kw.lower() in title_normalized
                for kw in filter_criteria.title_keywords
            ):
                return False

        # Transcript keyword filtering (most expensive, do last)
        if filter_criteria.transcript_keywords:
            sentences = raw_call.get('sentences', [])
            full_text = self._build_full_transcript(sentences).lower()
            if not any(kw.lower() in full_text for kw in filter_criteria.transcript_keywords):
                return False

        return True

    # ------------------------------------------------------------------
    # Main retrieval
    # ------------------------------------------------------------------
    def get_calls(
        self,
        filter_criteria: Optional[CallFilter] = None,
        include_transcript: bool = True,
        verbose: bool = True
    ) -> List[Call]:
        """
        Retrieve calls with filtering.

        Paginates until filter_criteria.limit *filtered* results are
        collected (or the API runs out of data).
        """
        if filter_criteria is None:
            filter_criteria = CallFilter()

        if verbose:
            print("🔍 Fetching calls with filters:")
            if filter_criteria.days_back:
                print(f"   📅 Last {filter_criteria.days_back} days")
            if filter_criteria.owner_emails:
                print(f"   👤 Owners: {', '.join(filter_criteria.owner_emails)}")
            if filter_criteria.attendee_emails:
                print(f"   👥 Attendees: {', '.join(filter_criteria.attendee_emails)}")
            if filter_criteria.title_keywords:
                print(f"   🔍 Title keywords: {', '.join(filter_criteria.title_keywords)}")
            if filter_criteria.transcript_keywords:
                print(f"   📝 Transcript keywords: {', '.join(filter_criteria.transcript_keywords)}")
            print()

        filtered_calls: List[Call] = []
        current_skip = filter_criteria.skip
        batch_size = 100
        max_raw = filter_criteria.limit * 10  # safety cap
        total_raw = 0

        while len(filtered_calls) < filter_criteria.limit:
            if verbose:
                print(f"   Fetching batch starting at {current_skip}...")

            batch = self.fetch_raw_transcripts(limit=batch_size, skip=current_skip)
            if not batch:
                break

            total_raw += len(batch)

            for raw_call in batch:
                if len(filtered_calls) >= filter_criteria.limit:
                    break
                if self._matches_filter(raw_call, filter_criteria):
                    call = Call(
                        id=raw_call['id'],
                        title=raw_call.get('title', 'Untitled'),
                        date=raw_call.get('date', ''),
                        duration=raw_call.get('duration', 0),
                        organizer_email=raw_call.get('organizer_email'),
                        attendees=raw_call.get('meeting_attendees', []),
                        transcript_url=raw_call.get('transcript_url'),
                        recording_url=raw_call.get('video_url'),
                        summary=raw_call.get('summary'),
                        sentences=raw_call.get('sentences', [])
                    )
                    if include_transcript:
                        call.full_transcript_text = self._build_full_transcript(
                            raw_call.get('sentences', [])
                        )
                    filtered_calls.append(call)

            if len(batch) < batch_size:
                break
            if total_raw >= max_raw:
                if verbose:
                    print(f"   Hit safety cap ({max_raw} raw calls). Stopping pagination.")
                break

            current_skip += len(batch)
            time.sleep(0.5)

        if verbose:
            print(f"   Retrieved {total_raw} raw calls, {len(filtered_calls)} match filters")
            print(f"   ✅ Returning {len(filtered_calls)} calls")
            print()

        return filtered_calls

    # ------------------------------------------------------------------
    # User discovery
    # ------------------------------------------------------------------
    def get_user_list(self, limit: int = 100) -> List[Dict[str, str]]:
        """Get unique users (organizers + attendees) from recent calls."""
        raw_calls = self.fetch_raw_transcripts(limit=limit)
        users: Dict[str, Dict[str, str]] = {}

        for call in raw_calls:
            org_email = call.get('organizer_email', '').lower()

            for attendee in call.get('meeting_attendees', []):
                email = (attendee.get('email') or '').lower()
                name = attendee.get('displayName') or attendee.get('name', '')
                if email:
                    existing = users.get(email)
                    if not existing or (name and existing.get('name') in ('', '(organizer)')):
                        users[email] = {'email': email, 'name': name}

            if org_email and org_email not in users:
                users[org_email] = {'email': org_email, 'name': '(organizer)'}

        return list(users.values())

    # ------------------------------------------------------------------
    # Feature Request Tracking
    # ------------------------------------------------------------------

    # Speakers to exclude from feature request scanning by default.
    # Matches against speaker_name (case-insensitive substring) and
    # attendee email domains. Add your team here so demo talk doesn't
    # get counted as prospect requests.
    DEFAULT_EXCLUDE_DOMAINS: List[str] = ["teachable.com"]
    DEFAULT_EXCLUDE_SPEAKERS: List[str] = ["zach mccall", "kevin", "jerome"]

    def _is_internal_speaker(
        self,
        speaker_name: str,
        call: "Call",
        exclude_domains: List[str],
        exclude_speakers: List[str],
    ) -> bool:
        """
        Check if a speaker is internal (should be excluded from feature tracking).

        Matches by:
          1. Speaker name against exclude_speakers (case-insensitive substring)
          2. Speaker name against attendee list to find their email,
             then checks email domain against exclude_domains
        """
        name_lower = speaker_name.lower()

        # Direct name match
        for excluded in exclude_speakers:
            if excluded.lower() in name_lower:
                return True

        # Try to resolve speaker to an attendee email, then check domain
        for attendee in call.attendees:
            attendee_name = (attendee.get('displayName') or attendee.get('name', '')).lower()
            attendee_email = (attendee.get('email') or '').lower()

            # Match speaker name to attendee name
            if attendee_name and (attendee_name in name_lower or name_lower in attendee_name):
                for domain in exclude_domains:
                    if domain.lower() in attendee_email:
                        return True

        return False

    def scan_feature_requests(
        self,
        calls: List[Call],
        keywords: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        exclude_speakers: Optional[List[str]] = None,
        context_chars: int = 200,
        verbose: bool = True
    ) -> FeatureRequestReport:
        """
        Scan calls for feature request mentions, excluding internal speakers.

        Args:
            calls: Call objects (with transcripts).
            keywords: Custom keyword list. Defaults to DEFAULT_FEATURE_KEYWORDS.
            exclude_domains: Email domains to exclude (e.g. ["teachable.com"]).
                Defaults to DEFAULT_EXCLUDE_DOMAINS.
            exclude_speakers: Speaker names to exclude (e.g. ["zach mccall"]).
                Defaults to DEFAULT_EXCLUDE_SPEAKERS.
            context_chars: Chars of surrounding text to capture.
            verbose: Print progress.

        Returns:
            FeatureRequestReport with counts and individual mentions.
        """
        if keywords is None:
            keywords = DEFAULT_FEATURE_KEYWORDS
        if exclude_domains is None:
            exclude_domains = self.DEFAULT_EXCLUDE_DOMAINS
        if exclude_speakers is None:
            exclude_speakers = self.DEFAULT_EXCLUDE_SPEAKERS

        if verbose:
            print(f"🔎 Scanning {len(calls)} calls for {len(keywords)} feature keywords...")
            if exclude_domains or exclude_speakers:
                excluded = exclude_domains + exclude_speakers
                print(f"   Excluding internal speakers: {', '.join(excluded)}")
            print()

        all_requests: List[FeatureRequest] = []
        keyword_counter: Counter = Counter()
        calls_with_matches: set = set()
        skipped_internal = 0

        for call in calls:
            if not call.sentences:
                continue
            for sentence in call.sentences:
                text = sentence.get('text', '')
                text_lower = text.lower()
                speaker = sentence.get('speaker_name', 'Unknown')

                # Skip internal speakers
                if self._is_internal_speaker(speaker, call, exclude_domains, exclude_speakers):
                    # Still check if keyword present, just for the skip count
                    for kw in keywords:
                        if kw.lower() in text_lower:
                            skipped_internal += 1
                            break
                    continue

                for kw in keywords:
                    if kw.lower() in text_lower:
                        keyword_counter[kw] += 1
                        calls_with_matches.add(call.id)

                        # Build timestamp and deep link
                        start_time = sentence.get('start_time')
                        ts_seconds = None
                        ts_display = ""
                        deep_link = None

                        if start_time is not None:
                            try:
                                ts_seconds = float(start_time)
                                mins = int(ts_seconds // 60)
                                secs = int(ts_seconds % 60)
                                ts_display = f"{mins}:{secs:02d}"

                                if call.transcript_url:
                                    base = call.transcript_url.split('?')[0]
                                    deep_link = f"{base}?t={ts_seconds:.0f}"
                            except (ValueError, TypeError):
                                pass

                        all_requests.append(FeatureRequest(
                            call_id=call.id,
                            call_title=call.title,
                            call_date=call.date,
                            speaker=speaker,
                            keyword_matched=kw,
                            surrounding_text=text[:context_chars],
                            timestamp_seconds=ts_seconds,
                            timestamp_display=ts_display,
                            transcript_url=call.transcript_url,
                            deep_link=deep_link,
                            attendee_emails=call.attendee_emails,
                        ))

        report = FeatureRequestReport(
            total_mentions=len(all_requests),
            unique_calls=len(calls_with_matches),
            keyword_counts=dict(keyword_counter),
            requests=all_requests,
        )

        if verbose:
            if skipped_internal:
                print(f"   Skipped {skipped_internal} mentions from internal speakers")
            report.print_summary()

        return report

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    def export_to_json(self, calls: List[Call], filename: str):
        """Export calls to JSON file."""
        data = [call.to_dict() for call in calls]
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✅ Exported {len(calls)} calls to {filename}")

    def export_to_csv(self, calls: List[Call], filename: str):
        """Export call metadata to CSV."""
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'ID', 'Title', 'Date', 'Duration (min)',
                'Organizer', 'Attendees', 'Transcript URL'
            ])
            for call in calls:
                writer.writerow([
                    call.id, call.title, call.date,
                    f"{call.duration_minutes:.1f}",
                    call.organizer_email or '',
                    '; '.join(call.attendee_names),
                    call.transcript_url or ''
                ])
        print(f"✅ Exported {len(calls)} calls to {filename}")

    def export_hubspot_notes(self, calls: List[Call], filename: str):
        """Export HubSpot-ready notes, one per call, separated by dividers."""
        with open(filename, 'w') as f:
            for i, call in enumerate(calls):
                if i > 0:
                    f.write("\n\n" + "=" * 70 + "\n\n")
                f.write(call.to_hubspot_note())
        print(f"✅ Exported {len(calls)} HubSpot notes to {filename}")

    def export_feature_report(self, report: FeatureRequestReport, filename: str):
        """Export feature request report to JSON."""
        with open(filename, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"✅ Exported feature request report to {filename}")

    def export_feature_report_csv(self, report: FeatureRequestReport, filename: str):
        """Export feature request mentions to CSV for spreadsheet analysis."""
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Call Date', 'Call Title', 'Speaker', 'Timestamp',
                'Keyword Matched', 'Context', 'Deep Link', 'Attendee Emails'
            ])
            for req in report.requests:
                writer.writerow([
                    req.call_date[:10] if req.call_date else '',
                    req.call_title,
                    req.speaker,
                    req.timestamp_display,
                    req.keyword_matched,
                    req.surrounding_text.replace('\n', ' '),
                    req.deep_link or '',
                    '; '.join(req.attendee_emails),
                ])
        print(f"✅ Exported {len(report.requests)} feature mentions to {filename}")

    def export_feature_dashboard(self, report: FeatureRequestReport, calls: List[Call], filename: str):
        """
        Generate an interactive HTML dashboard with two views:
          1. Features tab: aggregate feature counts, expand to see mentions with timestamps
          2. Calls tab: per-call view showing which features came up, with jump-to links
        """
        import html as html_mod

        # Build the data payload as JSON for client-side rendering.
        # This keeps the Python simple and lets JS handle interactivity.
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

        dashboard_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Feature Request Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

* {{ margin:0; padding:0; box-sizing:border-box; }}

body {{
    font-family: 'DM Sans', -apple-system, sans-serif;
    background: #0c0e14;
    color: #d0d3db;
    min-height: 100vh;
}}

/* Layout */
.wrap {{ max-width: 940px; margin: 0 auto; padding: 2rem 1.5rem; }}

/* Header */
.hdr {{ margin-bottom: 1.5rem; }}
.hdr h1 {{ font-size: 1.35rem; font-weight: 700; color: #f0f1f4; }}
.hdr .sub {{ font-size: 0.78rem; color: #555a6e; margin-top: 0.2rem; }}

/* Stats */
.stats {{ display: flex; gap: 0.75rem; margin-bottom: 1.5rem; }}
.st {{
    flex: 1; background: #14161e; border: 1px solid #1e2130;
    border-radius: 8px; padding: 1rem;
}}
.st .v {{
    font-size: 1.75rem; font-weight: 700; color: #fff;
    font-family: 'JetBrains Mono', monospace;
}}
.st .l {{
    font-size: 0.68rem; color: #555a6e; text-transform: uppercase;
    letter-spacing: 0.04em; margin-top: 0.15rem;
}}

/* Tabs */
.tabs {{
    display: flex; gap: 0; margin-bottom: 1.25rem;
    border-bottom: 1px solid #1e2130;
}}
.tab {{
    padding: 0.6rem 1.2rem; font-size: 0.82rem; font-weight: 500;
    color: #555a6e; cursor: pointer; border-bottom: 2px solid transparent;
    transition: all 0.15s; user-select: none;
}}
.tab:hover {{ color: #8b8fa3; }}
.tab.active {{ color: #f0f1f4; border-bottom-color: #4a7cff; }}

/* Search */
.search {{
    margin-bottom: 1rem;
}}
.search input {{
    width: 100%; padding: 0.6rem 0.85rem;
    background: #14161e; border: 1px solid #1e2130; border-radius: 6px;
    color: #d0d3db; font-family: 'DM Sans', sans-serif; font-size: 0.85rem;
    outline: none; transition: border-color 0.15s;
}}
.search input:focus {{ border-color: #4a7cff; }}
.search input::placeholder {{ color: #3a3d4e; }}

/* Cards (shared) */
.card {{
    background: #14161e; border: 1px solid #1e2130; border-radius: 8px;
    margin-bottom: 0.4rem; overflow: hidden; transition: border-color 0.15s;
}}
.card:hover {{ border-color: #2a2d40; }}

.card-hdr {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.7rem 0.9rem; cursor: pointer; user-select: none;
}}
.card-hdr .left {{ display: flex; align-items: center; gap: 0.5rem; }}
.card-hdr .arrow {{
    font-size: 0.55rem; color: #555a6e; transition: transform 0.15s;
    display: inline-block;
}}
.card.open .card-hdr .arrow {{ transform: rotate(90deg); }}
.card-hdr .name {{ font-weight: 500; font-size: 0.88rem; }}
.card-hdr .right {{
    display: flex; align-items: center; gap: 0.6rem; min-width: 120px;
}}
.bar {{
    height: 5px; border-radius: 3px; flex: 1;
    background: linear-gradient(90deg, #4a7cff, #6c5ce7); min-width: 0;
}}
.cnt {{
    font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;
    font-weight: 500; color: #6b7084; min-width: 1.5rem; text-align: right;
}}

/* Mention list */
.mentions {{ display: none; padding: 0 0.9rem 0.75rem 2rem; }}
.card.open .mentions {{ display: block; }}

.m-row {{
    padding: 0.55rem 0; border-top: 1px solid #1a1c28;
    display: flex; align-items: flex-start; gap: 0.6rem;
}}
.m-ts {{
    flex-shrink: 0; min-width: 3.8rem; padding-top: 0.05rem;
}}
.m-ts a, .m-ts span {{
    font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
    padding: 0.1rem 0.4rem; border-radius: 3px;
}}
.m-ts a {{
    color: #4a7cff; text-decoration: none;
    border: 1px solid #253060; transition: all 0.12s;
}}
.m-ts a:hover {{ background: #4a7cff; color: #fff; border-color: #4a7cff; }}
.m-ts span {{ color: #3a3d4e; }}

.m-body {{ flex: 1; min-width: 0; }}
.m-call {{
    font-size: 0.78rem; font-weight: 500; color: #9a9db0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.m-quote {{
    font-size: 0.76rem; color: #6b7084; line-height: 1.45; margin-top: 0.15rem;
}}
.m-spk {{ color: #8b8fa3; font-weight: 500; }}

/* Call card extras */
.call-meta {{
    display: flex; gap: 0.75rem; font-size: 0.73rem; color: #555a6e;
    font-family: 'JetBrains Mono', monospace; margin-bottom: 0.15rem;
}}
.call-link {{
    font-size: 0.73rem; color: #4a7cff; text-decoration: none;
}}
.call-link:hover {{ text-decoration: underline; }}

/* Feature tag inside call view */
.feat-tag {{
    display: inline-block; font-size: 0.68rem; font-weight: 500;
    padding: 0.1rem 0.45rem; border-radius: 3px;
    background: #1e2040; color: #7c8aff; margin-right: 0.3rem;
    margin-bottom: 0.15rem;
}}

/* Responsive */
@media (max-width: 600px) {{
    .stats {{ flex-wrap: wrap; }}
    .st {{ flex: 1 1 45%; }}
}}
</style>
</head>
<body>
<div class="wrap">
    <div class="hdr">
        <h1>Feature Requests</h1>
        <div class="sub" id="sub"></div>
    </div>
    <div class="stats" id="stats"></div>
    <div class="tabs">
        <div class="tab active" data-tab="features" onclick="switchTab('features')">By Feature</div>
        <div class="tab" data-tab="calls" onclick="switchTab('calls')">By Call</div>
    </div>
    <div class="search"><input id="q" placeholder="Search..." oninput="filter(this.value)"></div>
    <div id="features-view"></div>
    <div id="calls-view" style="display:none"></div>
</div>

<script>
const DATA = {data_json};

let currentTab = 'features';

function esc(s) {{
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}}

function tsLink(m) {{
    if (m.link) return '<a href="' + esc(m.link) + '" target="_blank" title="Jump to recording">' + esc(m.ts || '0:00') + '</a>';
    if (m.transcript) return '<a href="' + esc(m.transcript) + '" target="_blank">' + (m.ts || 'open') + '</a>';
    return '<span>' + esc(m.ts || '--') + '</span>';
}}

function mentionHTML(m, showFeature) {{
    let tag = showFeature ? '<span class="feat-tag">' + esc(m.keyword) + '</span> ' : '';
    return '<div class="m-row">' +
        '<div class="m-ts">' + tsLink(m) + '</div>' +
        '<div class="m-body">' +
            '<div class="m-call">' + tag + esc(m.call_title) + ' &middot; ' + m.call_date + '</div>' +
            '<div class="m-quote"><span class="m-spk">' + esc(m.speaker) + ':</span> "' + esc(m.text.slice(0, 140)) + '..."</div>' +
        '</div></div>';
}}

// ---- Features view ----
function buildFeatures() {{
    const groups = {{}};
    DATA.mentions.forEach(m => {{
        if (!groups[m.keyword]) groups[m.keyword] = [];
        groups[m.keyword].push(m);
    }});
    const sorted = Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
    const maxCount = sorted.length ? sorted[0][1].length : 1;

    let html = '';
    sorted.forEach(([kw, mentions]) => {{
        const barW = Math.max(8, (mentions.length / maxCount) * 100);
        const mSorted = mentions.sort((a, b) => (b.call_date || '').localeCompare(a.call_date || ''));

        let mHTML = '';
        mSorted.forEach(m => {{ mHTML += mentionHTML(m, false); }});

        html += '<div class="card" data-search="' + esc(kw.toLowerCase()) + ' ' + esc(mentions.map(m => m.speaker + ' ' + m.call_title + ' ' + m.text).join(' ').toLowerCase()) + '">' +
            '<div class="card-hdr" onclick="toggleCard(this)">' +
                '<div class="left"><span class="arrow">&#9654;</span><span class="name">' + esc(kw) + '</span></div>' +
                '<div class="right"><div class="bar" style="width:' + barW + '%"></div><span class="cnt">' + mentions.length + '</span></div>' +
            '</div>' +
            '<div class="mentions">' + mHTML + '</div></div>';
    }});
    document.getElementById('features-view').innerHTML = html || '<div style="padding:2rem;color:#3a3d4e;text-align:center">No feature requests found.</div>';
}}

// ---- Calls view ----
function buildCalls() {{
    // Group mentions by call
    const byCall = {{}};
    DATA.mentions.forEach(m => {{
        if (!byCall[m.call_id]) byCall[m.call_id] = [];
        byCall[m.call_id].push(m);
    }});

    // Get call metadata + sort by date desc
    const callList = DATA.calls
        .filter(c => byCall[c.id])
        .sort((a, b) => (b.date || '').localeCompare(a.date || ''));

    let html = '';
    callList.forEach(c => {{
        const mentions = byCall[c.id].sort((a, b) => (a.ts_sec || 0) - (b.ts_sec || 0));
        const features = [...new Set(mentions.map(m => m.keyword))];

        let tags = features.map(f => '<span class="feat-tag">' + esc(f) + '</span>').join('');

        let mHTML = '';
        mentions.forEach(m => {{ mHTML += mentionHTML(m, true); }});

        const transcriptLink = c.transcript ? ' <a class="call-link" href="' + esc(c.transcript) + '" target="_blank">Open full transcript</a>' : '';

        html += '<div class="card" data-search="' + esc((c.title + ' ' + features.join(' ') + ' ' + mentions.map(m => m.speaker + ' ' + m.text).join(' ')).toLowerCase()) + '">' +
            '<div class="card-hdr" onclick="toggleCard(this)">' +
                '<div class="left"><span class="arrow">&#9654;</span><span class="name">' + esc(c.title) + '</span></div>' +
                '<div class="right"><span class="cnt" style="min-width:auto">' + mentions.length + ' mention' + (mentions.length > 1 ? 's' : '') + '</span></div>' +
            '</div>' +
            '<div class="mentions">' +
                '<div style="margin-bottom:0.5rem">' +
                    '<div class="call-meta">' + c.date + ' &middot; ' + c.duration + ' min' + transcriptLink + '</div>' +
                    '<div>' + tags + '</div>' +
                '</div>' +
                mHTML +
            '</div></div>';
    }});

    document.getElementById('calls-view').innerHTML = html || '<div style="padding:2rem;color:#3a3d4e;text-align:center">No calls with feature requests found.</div>';
}}

function toggleCard(el) {{
    el.parentElement.classList.toggle('open');
}}

function switchTab(tab) {{
    currentTab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    document.getElementById('features-view').style.display = tab === 'features' ? '' : 'none';
    document.getElementById('calls-view').style.display = tab === 'calls' ? '' : 'none';
    document.getElementById('q').value = '';
    filter('');
}}

function filter(q) {{
    const low = q.toLowerCase();
    const view = currentTab === 'features' ? 'features-view' : 'calls-view';
    document.querySelectorAll('#' + view + ' .card').forEach(c => {{
        c.style.display = !low || c.dataset.search.includes(low) ? '' : 'none';
    }});
}}

// Init
document.getElementById('sub').textContent = 'Generated ' + DATA.stats.generated + '. ' + DATA.stats.total_mentions + ' mentions across ' + DATA.stats.unique_calls + ' calls.';
document.getElementById('stats').innerHTML =
    '<div class="st"><div class="v">' + DATA.stats.total_mentions + '</div><div class="l">Mentions</div></div>' +
    '<div class="st"><div class="v">' + DATA.stats.unique_calls + '</div><div class="l">Calls</div></div>' +
    '<div class="st"><div class="v">' + DATA.stats.unique_features + '</div><div class="l">Features</div></div>';

buildFeatures();
buildCalls();
</script>
</body>
</html>"""

        with open(filename, 'w') as f:
            f.write(dashboard_html)
        print(f"✅ Exported feature dashboard to {filename}")


def main():
    """Example usage"""
    import os

    api_key = os.getenv('FIREFLIES_API_KEY')
    if not api_key:
        print("Please set FIREFLIES_API_KEY environment variable")
        return

    retriever = FirefliesRetriever(api_key)

    calls = retriever.get_calls(
        filter_criteria=CallFilter(days_back=30, limit=10)
    )

    for call in calls:
        print(call.to_hubspot_note())
        print("\n" + "=" * 70 + "\n")

    report = retriever.scan_feature_requests(calls)

    if calls:
        retriever.export_to_json(calls, "calls.json")
        retriever.export_to_csv(calls, "calls.csv")
        retriever.export_hubspot_notes(calls, "hubspot_notes.txt")
        retriever.export_feature_report(report, "feature_requests.json")
        retriever.export_feature_report_csv(report, "feature_requests.csv")


if __name__ == "__main__":
    main()
