"""
Fireflies GraphQL API client with filtering, pagination, and rate-limit awareness.
"""

# ---------------------------------------------------------------------------
# CACHING LAYER — hook points for future implementation
# ---------------------------------------------------------------------------
# Plan: save raw API results to a local JSON cache keyed by query params.
#   - Cache file: .fireflies_cache/<hash_of_query_params>.json
#   - Each entry stores: {"fetched_at": <ISO timestamp>, "data": [raw transcripts]}
#   - On fetch: check cache first. If cache file exists and fetched_at < 7 days
#     old, return cached data instead of calling API.
#   - CLI flag: --force bypasses cache (deletes stale entry, fetches fresh).
#   - Hook points in this file:
#     1. fetch_raw_transcripts() — check cache before API call, write to cache after
#     2. get_calls() — pass-through, no changes needed (uses fetch_raw_transcripts)
#     3. __init__() — accept cache_dir and cache_ttl_days params
# ---------------------------------------------------------------------------

import requests
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from collections import Counter

from models import (
    Call, CallFilter, FeatureRequest, FeatureRequestReport,
    DEFAULT_FEATURE_KEYWORDS, build_keyword_patterns, load_blacklist,
)


class FirefliesRetriever:
    """Robust layer for retrieving and filtering Fireflies calls."""

    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.0       # seconds; doubles each retry
    REQUEST_DELAY = 1.0       # seconds between paginated batches
    RATE_LIMIT_WAIT = 10.0    # default wait when 429 has no Retry-After
    HARD_CAP_RAW = 500        # absolute max raw calls per run, regardless of limit

    # Speakers to exclude from feature request scanning by default.
    DEFAULT_EXCLUDE_DOMAINS: List[str] = ["teachable.com"]
    DEFAULT_EXCLUDE_SPEAKERS: List[str] = ["zach mccall", "kevin", "jerome"]

    def __init__(self, api_key: str, request_delay: float = None):
        self.api_key = api_key
        self.base_url = "https://api.fireflies.ai/graphql"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if request_delay is not None:
            self.REQUEST_DELAY = request_delay

        # API usage counters — reset at the start of each get_calls() run
        self.api_calls_made = 0
        self.raw_transcripts_fetched = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _make_request(self, query: str, variables: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make a GraphQL request with retry, backoff, and rate-limit handling."""
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
                    timeout=30,
                )

                # Rate-limit handling
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else self.RATE_LIMIT_WAIT
                    print(f"   Rate limited. Waiting {wait:.0f}s before retry...")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                data = response.json()

                if "errors" in data:
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
            # Cache hook: write raw results to cache here
            results = data.get("data", {}).get("transcripts", [])
            self.api_calls_made += 1
            self.raw_transcripts_fetched += len(results)
            return results
        except Exception as e:
            print(f"Error fetching transcripts: {e}")
            return []

    def _build_full_transcript(self, sentences: List[Dict[str, Any]]) -> str:
        if not sentences:
            return ""
        return "\n".join(
            f"{s.get('speaker_name', 'Unknown')}: {s.get('text', '')}"
            for s in sentences
        )

    def _parse_call_date(self, date_val) -> Optional[datetime]:
        """Parse a call date — handles both epoch-ms (int/str) and ISO strings."""
        if not date_val:
            return None
        try:
            # Fireflies returns epoch milliseconds as int or numeric string
            ts = float(date_val)
            if ts > 1e12:  # milliseconds
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
        # Fallback: ISO string
        try:
            cleaned = str(date_val).replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            return None

    def _matches_filter(self, raw_call: Dict[str, Any], filter_criteria: CallFilter) -> bool:
        # Date
        if filter_criteria.start_date or filter_criteria.end_date:
            call_date = self._parse_call_date(raw_call.get("date", ""))
            if call_date is None:
                return False
            if filter_criteria.start_date and call_date < filter_criteria.start_date:
                return False
            if filter_criteria.end_date and call_date > filter_criteria.end_date:
                return False

        # Duration
        duration = raw_call.get("duration", 0)
        if filter_criteria.min_duration is not None and duration < filter_criteria.min_duration:
            return False
        if filter_criteria.max_duration is not None and duration > filter_criteria.max_duration:
            return False

        # Owner
        if filter_criteria.owner_emails:
            organizer = raw_call.get("organizer_email", "").lower()
            if not any(o.lower() in organizer for o in filter_criteria.owner_emails):
                return False

        # Attendee
        if filter_criteria.attendee_emails:
            emails = [a.get("email", "").lower() for a in raw_call.get("meeting_attendees", [])]
            if not any(any(fe.lower() in ae for ae in emails) for fe in filter_criteria.attendee_emails):
                return False

        # Title keywords (normalize hyphens/spaces)
        if filter_criteria.title_keywords:
            title = raw_call.get("title", "").lower()
            title_norm = title.replace("-", " ").replace("_", " ")
            if not any(kw.lower() in title or kw.lower() in title_norm for kw in filter_criteria.title_keywords):
                return False

        # Transcript keywords (most expensive — do last)
        if filter_criteria.transcript_keywords:
            sentences = raw_call.get("sentences", [])
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
        verbose: bool = True,
    ) -> List[Call]:
        """
        Retrieve calls with filtering.

        Paginates until filter_criteria.limit *filtered* results are
        collected (or the API runs out of data).
        """
        if filter_criteria is None:
            filter_criteria = CallFilter()

        if verbose:
            print("Fetching calls with filters:")
            if filter_criteria.days_back:
                print(f"   Last {filter_criteria.days_back} days")
            if filter_criteria.owner_emails:
                print(f"   Owners: {', '.join(filter_criteria.owner_emails)}")
            if filter_criteria.attendee_emails:
                print(f"   Attendees: {', '.join(filter_criteria.attendee_emails)}")
            if filter_criteria.title_keywords:
                print(f"   Title keywords: {', '.join(filter_criteria.title_keywords)}")
            if filter_criteria.transcript_keywords:
                print(f"   Transcript keywords: {', '.join(filter_criteria.transcript_keywords)}")
            print()

        # Reset API counters for this run
        self.api_calls_made = 0
        self.raw_transcripts_fetched = 0

        filtered_calls: List[Call] = []
        current_skip = filter_criteria.skip
        batch_size = 50  # Fireflies API max per request
        soft_cap = filter_criteria.limit * 20
        max_raw = min(soft_cap, self.HARD_CAP_RAW)
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
                    # Convert epoch-ms date to ISO string for display
                    parsed_dt = self._parse_call_date(raw_call.get("date", ""))
                    date_iso = parsed_dt.isoformat() if parsed_dt else str(raw_call.get("date", ""))

                    call = Call(
                        id=raw_call["id"],
                        title=raw_call.get("title", "Untitled"),
                        date=date_iso,
                        duration=raw_call.get("duration", 0),
                        organizer_email=raw_call.get("organizer_email"),
                        attendees=raw_call.get("meeting_attendees", []),
                        transcript_url=raw_call.get("transcript_url"),
                        recording_url=raw_call.get("video_url"),
                        summary=raw_call.get("summary"),
                        sentences=raw_call.get("sentences", []),
                    )
                    if include_transcript:
                        call.full_transcript_text = self._build_full_transcript(
                            raw_call.get("sentences", [])
                        )
                    filtered_calls.append(call)

            if len(batch) < batch_size:
                break
            if total_raw >= max_raw:
                if total_raw >= self.HARD_CAP_RAW:
                    if verbose:
                        print(f"   Hit hard safety cap ({self.HARD_CAP_RAW} raw calls). Stopping pagination.")
                else:
                    if verbose:
                        print(f"   Hit safety cap ({max_raw} raw calls, limit*20). Stopping pagination.")
                break

            current_skip += len(batch)
            time.sleep(self.REQUEST_DELAY)

        if verbose:
            print(f"   Retrieved {total_raw} raw calls, {len(filtered_calls)} match filters")
            print(f"   Returning {len(filtered_calls)} calls")
            print(f"   API calls made: {self.api_calls_made} ({self.raw_transcripts_fetched} raw transcripts fetched)")
            print()

        return filtered_calls

    # ------------------------------------------------------------------
    # User discovery
    # ------------------------------------------------------------------
    def get_user_list(self, limit: int = 100) -> List[Dict[str, str]]:
        raw_calls = self.fetch_raw_transcripts(limit=limit)
        users: Dict[str, Dict[str, str]] = {}

        for call in raw_calls:
            for attendee in call.get("meeting_attendees", []):
                email = (attendee.get("email") or "").lower()
                name = attendee.get("displayName") or attendee.get("name", "")
                if email:
                    existing = users.get(email)
                    if not existing or (name and existing.get("name") in ("", "(organizer)")):
                        users[email] = {"email": email, "name": name}

            org_email = call.get("organizer_email", "").lower()
            if org_email and org_email not in users:
                users[org_email] = {"email": org_email, "name": "(organizer)"}

        return list(users.values())

    # ------------------------------------------------------------------
    # Feature Request Tracking
    # ------------------------------------------------------------------
    def _is_internal_speaker(
        self,
        speaker_name: str,
        call: Call,
        exclude_domains: List[str],
        exclude_speakers: List[str],
    ) -> bool:
        name_lower = speaker_name.lower()

        for excluded in exclude_speakers:
            if excluded.lower() in name_lower:
                return True

        for attendee in call.attendees:
            att_name = (attendee.get("displayName") or attendee.get("name") or "").lower()
            att_email = (attendee.get("email") or "").lower()
            if att_name and (att_name in name_lower or name_lower in att_name):
                for domain in exclude_domains:
                    if domain.lower() in att_email:
                        return True

        return False

    def scan_feature_requests(
        self,
        calls: List[Call],
        keywords: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        exclude_speakers: Optional[List[str]] = None,
        blacklist: Optional[List[str]] = None,
        context_chars: int = 200,
        verbose: bool = True,
    ) -> FeatureRequestReport:
        if keywords is None:
            keywords = DEFAULT_FEATURE_KEYWORDS
        if exclude_domains is None:
            exclude_domains = self.DEFAULT_EXCLUDE_DOMAINS
        if exclude_speakers is None:
            exclude_speakers = self.DEFAULT_EXCLUDE_SPEAKERS
        if blacklist is None:
            blacklist = load_blacklist()

        # Normalize blacklist for case-insensitive comparison
        blacklist_lower = {term.lower() for term in blacklist}

        # Pre-compile keyword patterns (word-boundary for acronyms)
        kw_patterns = build_keyword_patterns(keywords)

        if verbose:
            print(f"Scanning {len(calls)} calls for {len(keywords)} feature keywords...")
            if exclude_domains or exclude_speakers:
                excluded = exclude_domains + exclude_speakers
                print(f"   Excluding internal speakers: {', '.join(excluded)}")
            if blacklist_lower:
                print(f"   Blacklisted keywords: {', '.join(sorted(blacklist_lower))}")
            print()

        all_requests: List[FeatureRequest] = []
        keyword_counter: Counter = Counter()
        calls_with_matches: set = set()
        skipped_internal = 0

        # Dedup: track processed call IDs to avoid scanning the same call twice.
        # This is where cache-based dedup will plug in — when loading calls from
        # both cache and fresh API results, check this set to skip already-processed
        # calls and avoid duplicate feature request entries.
        processed_call_ids: set = set()

        for call in calls:
            if call.id in processed_call_ids:
                continue
            processed_call_ids.add(call.id)
            if not call.sentences:
                continue

            # Per-call keyword dedup: only count each keyword once per call.
            # Keeps the first (earliest) occurrence as the representative mention.
            seen_keywords_this_call: set = set()

            for sentence in call.sentences:
                text = sentence.get("text", "")
                speaker = sentence.get("speaker_name", "Unknown")

                if self._is_internal_speaker(speaker, call, exclude_domains, exclude_speakers):
                    for _kw, pat in kw_patterns:
                        if pat.search(text):
                            skipped_internal += 1
                            break
                    continue

                for kw, pat in kw_patterns:
                    if kw.lower() in blacklist_lower:
                        continue
                    if kw in seen_keywords_this_call:
                        continue
                    if pat.search(text):
                        seen_keywords_this_call.add(kw)
                        keyword_counter[kw] += 1
                        calls_with_matches.add(call.id)

                        start_time = sentence.get("start_time")
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
                                    base = call.transcript_url.split("?")[0]
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
