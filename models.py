"""
Data models for Fireflies call retrieval.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from collections import Counter


# ---------------------------------------------------------------------------
# Default feature request keywords to scan for in transcripts.
# Add/remove terms here or override per-call via scan_feature_requests().
#
# Short keywords (<=5 chars or acronyms) get word-boundary matching
# automatically.  Longer phrases use exact substring matching.
# ---------------------------------------------------------------------------
DEFAULT_FEATURE_KEYWORDS: List[str] = [
    # Explicit feature-request signals
    "feature request",
    "missing feature",
    "wish list",
    "wishlist",
    "on the roadmap",
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

# ---------------------------------------------------------------------------
# Blacklisted feature keywords — matches on these terms are skipped.
# Managed via --blacklist-add / --blacklist-remove CLI flags, which write to
# .feature_blacklist (one term per line). This in-code list is merged with
# the file at runtime.
# ---------------------------------------------------------------------------
BLACKLISTED_FEATURES: List[str] = []


def load_blacklist(filepath: str = ".feature_blacklist") -> List[str]:
    """Load blacklisted terms from file, merged with in-code BLACKLISTED_FEATURES."""
    terms = list(BLACKLISTED_FEATURES)
    try:
        with open(filepath, "r") as f:
            for line in f:
                term = line.strip()
                if term and term not in terms:
                    terms.append(term)
    except FileNotFoundError:
        pass
    return terms


def _build_keyword_pattern(keyword: str) -> re.Pattern:
    """
    Build a compiled regex for a keyword with word-boundary anchors.

    All keywords get \\b anchors so that partial-word matches are prevented
    (e.g. "SSO" won't match "association", "CE credits" won't match
    "service credits").
    """
    escaped = re.escape(keyword)
    return re.compile(r"\b" + escaped + r"\b", re.IGNORECASE)


def build_keyword_patterns(keywords: List[str]) -> List[tuple]:
    """Return list of (keyword_str, compiled_pattern) tuples."""
    return [(kw, _build_keyword_pattern(kw)) for kw in keywords]


@dataclass
class CallFilter:
    """Filter criteria for retrieving calls."""
    # Date filtering
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    days_back: Optional[int] = 14

    # Ownership filtering
    owner_emails: Optional[List[str]] = None
    attendee_emails: Optional[List[str]] = None

    # Keyword filtering
    title_keywords: Optional[List[str]] = None
    transcript_keywords: Optional[List[str]] = None

    # Metadata filtering
    min_duration: Optional[int] = None   # seconds
    max_duration: Optional[int] = None   # seconds

    # Pagination
    limit: int = 100
    skip: int = 0

    def __post_init__(self):
        if self.days_back and not self.start_date:
            self.end_date = datetime.now(timezone.utc)
            self.start_date = self.end_date - timedelta(days=self.days_back)


@dataclass
class FeatureRequest:
    """A single feature request mention found in a transcript."""
    call_id: str
    call_title: str
    call_date: str
    speaker: str
    keyword_matched: str
    surrounding_text: str
    timestamp_seconds: Optional[float] = None
    timestamp_display: str = ""
    transcript_url: Optional[str] = None
    deep_link: Optional[str] = None
    attendee_emails: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureRequestReport:
    """Aggregated feature request data across calls."""
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

    def _group_by_keyword(self) -> Dict[str, List[FeatureRequest]]:
        groups: Dict[str, List[FeatureRequest]] = {}
        for req in self.requests:
            groups.setdefault(req.keyword_matched, []).append(req)
        return dict(sorted(groups.items(), key=lambda x: -len(x[1])))

    def print_summary(self, expand: bool = True, max_mentions_per_keyword: int = 5):
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
            bar = "\u2588" * min(len(mentions), 30)
            print(f"  {len(mentions):>3}x  {kw:<35s} {bar}")

            if expand:
                sorted_mentions = sorted(mentions, key=lambda r: r.call_date or "", reverse=True)
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
    """Structured representation of a Fireflies call."""
    id: str
    title: str
    date: str
    duration: int   # seconds
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

    def to_hubspot_note(
        self,
        include_action_items: bool = True,
        feature_requests: Optional[List["FeatureRequest"]] = None,
    ) -> str:
        lines: List[str] = []

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

        overview = ""
        if self.summary:
            overview = self.summary.get('overview', '') or ''
        lines.append("SUMMARY")
        lines.append(overview.strip() if overview.strip() else "(No summary available)")

        if include_action_items and self.summary:
            action_items = self.summary.get('action_items') or []
            # Fireflies API returns action_items as a string, not a list
            if isinstance(action_items, str):
                action_items = [line.strip().strip("*-•").strip()
                                for line in action_items.split("\n")
                                if line.strip()]
            if action_items:
                lines.append("---")
                lines.append("ACTION ITEMS")
                for item in action_items:
                    if isinstance(item, str):
                        lines.append(f"- {item}")
                    elif isinstance(item, dict):
                        lines.append(f"- {item.get('text', str(item))}")

        if self.summary:
            keywords = self.summary.get('keywords') or []
            # Fireflies API may return keywords as a string
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            if keywords:
                lines.append("---")
                lines.append("KEY TOPICS")
                lines.append(", ".join(keywords))

        # Feature requests section (from AI analysis or keyword scanner)
        if feature_requests:
            lines.append("---")
            lines.append("FEATURE REQUESTS")
            for req in feature_requests:
                ts_part = f" ({req.timestamp_display})" if req.timestamp_display else ""
                quote = req.surrounding_text[:120].replace("\n", " ")
                lines.append(f"- {req.keyword_matched}{ts_part} - \"{quote}\"")

        if self.transcript_url:
            lines.append("---")
            # NOTE: Fireflies has no public share URL. The /view/ link requires
            # a Fireflies account. Checked the GraphQL schema — no share_url or
            # share_link field exists. /share/<id> returns 404. Revisit if
            # Fireflies adds a share endpoint to their API.
            lines.append(f"TRANSCRIPT: {self.transcript_url}")

        return "\n".join(lines)
