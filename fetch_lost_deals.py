#!/usr/bin/env python3
"""
Fetch closed-won and closed-lost deals from HubSpot and write win_loss.json.

Feature signals come from HubSpot structured close-time fields only:
  - notes = closed-won deal narrative
  - loss_reason = Product Limitation marks product-limitation scope
  - notes_on_customer_feedback (stored verbatim)
Competitor and loss type data come from structured HubSpot fields.
No Anthropic API key required for default operation.

Optional: --api-extract enhances feedback notes with AI feature extraction.

Reuses pipeline/search helpers from fetch_performance.py.

Usage:
    python3 fetch_lost_deals.py --days 180 --out test_output/win_loss.json
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
HUBSPOT_BASE = "https://api.hubspot.com"

# Reuse helpers from fetch_performance
from fetch_performance import (
    pull_hubspot_pipeline_stages,
    pull_hubspot_owners,
    _hs_search_all,
    _hs_headers,
)

DEFAULT_LOST_AMOUNT = 6000.0  # Teachable base enterprise cost estimate

# Confirmed dropdown options from HubSpot product_feedback property (May 2026).
# Only values in this set count as confirmed feature gaps.
# Update if new options are added to the HubSpot dropdown.
PRODUCT_FEEDBACK_OPTIONS = {
    "SCORM Support",
    "Certificate Issuance",
    "Progress Tracking",
    "Course Enrollment Limits",
    "Waitlist",
    "Large File Upload",
    "Course Integration",
    "Discussion Board/Forum",
    "Website Builder Integration",
    "Bulk Content Upload",
    "Setup in User Language (Spanish)",
    "Coaching Workspace",
    "Coaching Notes",
    "Coaching Accountability",
    "Coaching Automation",
    "Zoom Integration",
    "One-Click Access (No Login Required)",
    "Multi-Level Admin Hierarchy (Organizations)",
    "Centralized / Org-Level Reporting",
    "Attendance Reporting (Compliance Use Case)",
    "Personalized Learning Paths (Rules-Based)",
    "Conditional Content Visibility (Per User)",
    "Quiz-Based Routing & Enrollment",
    "Org-Level Learning Analytics (Journey View)",
    "Centralized Knowledge Management",
    "EU Data Residency (Regional Data Hosting Requirement)",
    "Embedded Learning Experience (No Separate Login)",
}
UNCLASSIFIED_PRODUCT_LIMITATION_FEATURE = "Product Limitation - Needs Review"
# Only applied to Sales pipeline lost deals (Discovery stage or later by definition,
# since Discovery is the first stage in the Sales pipeline). Pre Sales deals are
# excluded by pipeline filter. Substance gating (MIN_NOTE_CHARS) further ensures
# these are real evaluations, not tire-kickers.
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.win_loss_cache.json')

# Minimum combined note length (sanitized chars) for a deal to qualify.
MIN_NOTE_CHARS = 150

# Terms that make a short note worth extracting regardless of length
KEYWORD_OVERRIDE_TERMS = {
    # Competitors
    'kajabi', 'docebo', 'thinkific', 'learnupon', 'litmos', 'cornerstone',
    'talent lms', 'talentlms', 'absorb', 'bridge', 'canvas', 'moodle',
    'skool', 'mighty networks', 'podia', 'kartra', 'systeme',
    # Pricing signals
    'pricing', 'price', 'cost', 'budget', 'expensive', 'affordable', 'discount',
    'package', 'tier', 'plan', 'roi', 'quote',
    # Close reason signals
    'lost to', 'went with', 'chose', 'decided on', 'missing', 'gap', 'lack',
    'won because', 'closed because', 'key factor', 'deal breaker',
    # Canonical feature fragments (lowercase)
    'custom domain', 'white label', 'sso', 'saml', 'org hierarchy', 'reporting',
    'certificate', 'mobile app', 'api', 'webhook', 'salesforce', 'integration',
    'multi-tenant', 'organization',
    'product limitation', 'prerequisite', 'push notification', 'wishlist',
}


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def get_outcome_stage_ids(stage_map: dict, pipelines: list, pipeline_label: str = 'Sales') -> dict:
    """Return {'WON': [stage_ids], 'LOST': [stage_ids]} for the named pipeline.

    Uses exact match on pipeline label to avoid "Sales" matching "Pre Sales".
    """
    won_ids, lost_ids = [], []
    for pp in pipelines:
        if pp['label'].strip().lower() != pipeline_label.strip().lower():
            continue
        for sid in pp['stage_ids']:
            label = stage_map.get(sid, '').upper()
            if label == 'WON':
                won_ids.append(sid)
            elif label == 'LOST':
                lost_ids.append(sid)
    return {'WON': won_ids, 'LOST': lost_ids}


# ---------------------------------------------------------------------------
# Deal fetching
# ---------------------------------------------------------------------------

def pull_closed_deals(token: str, since: datetime, outcome_ids: dict) -> list[dict]:
    """Pull all closed-won and closed-lost deals since `since`."""
    all_stage_ids = outcome_ids['WON'] + outcome_ids['LOST']
    if not all_stage_ids:
        logger.warning("No WON/LOST stage IDs found")
        return []
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": "closedate", "operator": "GTE",
             "value": str(int(since.timestamp() * 1000))},
            {"propertyName": "dealstage", "operator": "IN",
             "values": all_stage_ids},
        ]}],
        "properties": [
            "dealname", "amount", "dealstage", "closedate",
            "hubspot_owner_id", "pipeline", "createdate",
            "hs_closed_lost_reason",
            # Structured deal properties (primary signal source when populated)
            "notes",                  # closed-won narrative field
            "notes_on_customer_feedback",
            "product_feedback",       # multi-select
            "loss_reason",
            "loss_type",
            "kb_or_pd_deal",
            "uses_competitor_platform",
            "competitor_platform",
            "lead_source",
        ],
        "limit": 100,
        "sorts": [{"propertyName": "closedate", "direction": "DESCENDING"}],
    }
    return _hs_search_all(token, "deals", body)


# ---------------------------------------------------------------------------
# Note sanitization and gating
# ---------------------------------------------------------------------------

def _sanitize_note(raw: str) -> str:
    """Strip HTML tags, unescape entities, normalize whitespace."""
    text = re.sub(r'<[^>]+>', ' ', raw)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _note_has_keyword_override(text: str) -> bool:
    """Return True if the sanitized note contains any high-value terms."""
    lower = text.lower()
    return any(term in lower for term in KEYWORD_OVERRIDE_TERMS)


def _parse_product_feedback(raw: str) -> list[str]:
    """Parse product_feedback multi-select into a confirmed list of feature names.

    HubSpot returns multi-select values as semicolon-separated strings.
    Only returns values present in PRODUCT_FEEDBACK_OPTIONS.
    """
    if not raw:
        return []
    values    = [v.strip() for v in raw.split(";") if v.strip()]
    confirmed = [v for v in values if v in PRODUCT_FEEDBACK_OPTIONS]
    unknown   = [v for v in values if v not in PRODUCT_FEEDBACK_OPTIONS]
    if unknown:
        logger.warning(
            "product_feedback contained unrecognised values: %s -- "
            "check if new options were added to HubSpot and update PRODUCT_FEEDBACK_OPTIONS",
            unknown,
        )
    return confirmed


# Keyword variants that map to canonical PRODUCT_FEEDBACK_OPTIONS names.
# Lowercase fragments -> canonical name. Checked against feedback notes text.
_FEATURE_KEYWORD_MAP = {
    "scorm": "SCORM Support",
    "certificate": "Certificate Issuance",
    "progress track": "Progress Tracking",
    "enrollment limit": "Course Enrollment Limits",
    "waitlist": "Waitlist",
    "large file": "Large File Upload",
    "file upload": "Large File Upload",
    "course integration": "Course Integration",
    "discussion board": "Discussion Board/Forum",
    "forum": "Discussion Board/Forum",
    "website builder": "Website Builder Integration",
    "bulk upload": "Bulk Content Upload",
    "bulk content": "Bulk Content Upload",
    "spanish": "Setup in User Language (Spanish)",
    "user language": "Setup in User Language (Spanish)",
    "coaching workspace": "Coaching Workspace",
    "coaching notes": "Coaching Notes",
    "coaching accountability": "Coaching Accountability",
    "coaching automation": "Coaching Automation",
    "zoom": "Zoom Integration",
    "one-click access": "One-Click Access (No Login Required)",
    "no login": "One-Click Access (No Login Required)",
    "admin hierarchy": "Multi-Level Admin Hierarchy (Organizations)",
    "multi-level admin": "Multi-Level Admin Hierarchy (Organizations)",
    "org hierarchy": "Multi-Level Admin Hierarchy (Organizations)",
    "org-level reporting": "Centralized / Org-Level Reporting",
    "centralized reporting": "Centralized / Org-Level Reporting",
    "attendance report": "Attendance Reporting (Compliance Use Case)",
    "compliance": "Attendance Reporting (Compliance Use Case)",
    "learning path": "Personalized Learning Paths (Rules-Based)",
    "personalized learning": "Personalized Learning Paths (Rules-Based)",
    "conditional content": "Conditional Content Visibility (Per User)",
    "content visibility": "Conditional Content Visibility (Per User)",
    "quiz-based routing": "Quiz-Based Routing & Enrollment",
    "quiz routing": "Quiz-Based Routing & Enrollment",
    "learning analytics": "Org-Level Learning Analytics (Journey View)",
    "journey view": "Org-Level Learning Analytics (Journey View)",
    "knowledge management": "Centralized Knowledge Management",
    "eu data residency": "EU Data Residency (Regional Data Hosting Requirement)",
    "data residency": "EU Data Residency (Regional Data Hosting Requirement)",
    "regional data": "EU Data Residency (Regional Data Hosting Requirement)",
    "embedded learning": "Embedded Learning Experience (No Separate Login)",
    "no separate login": "Embedded Learning Experience (No Separate Login)",
    "prerequisite": "Course Sequencing / Completion Gating",
    "prerequisites": "Course Sequencing / Completion Gating",
    "lock course": "Course Sequencing / Completion Gating",
    "locked course": "Course Sequencing / Completion Gating",
    "sequential course": "Course Sequencing / Completion Gating",
    "in-person course": "Hybrid Scheduling",
    "in person course": "Hybrid Scheduling",
    "on-site class": "Hybrid Scheduling",
    "onsite class": "Hybrid Scheduling",
    "hybrid": "Hybrid Scheduling",
    "push notification": "Push Notifications",
    "push notifications": "Push Notifications",
    "targeted notification": "Push Notifications",
    "segmented push": "Push Notifications",
    "wishlist": "Wishlist / Favorites",
    "wish list": "Wishlist / Favorites",
    "favorites": "Wishlist / Favorites",
    "save course": "Wishlist / Favorites",
    "bookmark": "Wishlist / Favorites",
    "tracking capabilities": "Completion Tracking",
    "track completion": "Completion Tracking",
    "tracking completion": "Completion Tracking",
    "time spent on specific videos": "Completion Tracking",
    "accountability tracking": "Completion Tracking",
    "high levels of accountability": "Completion Tracking",
    "custom work": "Custom Implementation / Expert Services",
    "expert services": "Custom Implementation / Expert Services",
    "build their tool internally": "Custom Implementation / Expert Services",
    "manga": "Manga / Document Reader",
    "pdf formatting": "Manga / Document Reader",
    "community-first": "Community Features",
    "home for her community": "Community Features",
    "audience lives": "Community Features",
    "discord": "Community Features",
    "facebook groups": "Community Features",
    "direct/group messaging": "Direct Messaging",
    "group chats": "Direct Messaging",
    "whatsapp-style": "Direct Messaging",
    "coaching calendar": "Hybrid Scheduling",
    "upcoming sessions": "Hybrid Scheduling",
    "habit tracker": "Coaching Accountability",
    "diary-style": "Coaching Accountability",
    "call logging": "Completion Tracking",
    "track completed sessions": "Completion Tracking",
    "white label app": "White Label Mobile App",
    "language functionality": "Localization / Multi-language Support",
    "language functionalitaty": "Localization / Multi-language Support",
    "marketplace": "Marketplace / Student Acquisition",
    "bring students": "Marketplace / Student Acquisition",
    "guarantees on registrations": "Marketplace / Student Acquisition",
    "conversion tracking": "Reporting Dashboard",
    "4k video": "Video Hosting",
    "h.265": "Video Hosting",
    "hevc": "Video Hosting",
    "flac audio": "Video Hosting",
    "youtube shopping": "YouTube Shopping Integration",
    "shopify product tagging": "YouTube Shopping Integration",
    "zapier reliability": "Zapier Integration",
    "zapier automations": "Zapier Integration",
    "embedding questions into videos": "Interactive Video",
    "questions into videos": "Interactive Video",
    "quiz question types": "Quiz Question Types",
    "question/answer types": "Quiz Question Types",
    "technical assessments": "Quiz / Assessment Builder",
    "technical assessment": "Quiz / Assessment Builder",
    "one attempt only": "Quiz / Assessment Builder",
    "minimum passing score": "Quiz / Assessment Builder",
    "only students who pass": "Registration Gating",
    "higher level of customization": "Brand Customization",
    "course builder customization": "Brand Customization",
    "native automation": "Automation Features",
    "native automation features": "Automation Features",
    "integrate with our website shopify": "Embedded Course Widget",
    "integrate with our website": "Embedded Course Widget",
    "separate site": "One-Click Access (No Login Required)",
    "stripe express dashboard": "Stripe Express Dashboard Limitations",
    "main stripe dashboard": "Stripe Express Dashboard Limitations",
    "organizations feature": "Organizations / Multi-tenancy",
    "custom student experience": "Per-Student Course Customization by Coach",
    "tags/metadata": "Quiz-Based Routing & Enrollment",
    "automatically route": "Quiz-Based Routing & Enrollment",
    "guided sequence": "Learning Paths",
    "no catalog browsing": "Learning Paths",
    "duolingo-style": "Learning Paths",
    "ai-contect": "AI Course Generation",
    "ai-content": "AI Course Generation",
    "ai content": "AI Course Generation",
    "ai platform": "AI Course Generation",
    "help them create videos": "AI Course Generation",
}


def _extract_features_from_notes(feedback_notes: str, outcome: str) -> list[dict]:
    """Extract features from notes_on_customer_feedback using local keyword matching.

    Matches against canonical feature names (exact, case-insensitive) and
    keyword variants. No API call needed.
    """
    if not feedback_notes:
        return []

    lower = feedback_notes.lower()
    found: dict[str, str | None] = {}  # canonical name -> quote snippet or None

    # Check canonical names directly (case-insensitive)
    for canonical in PRODUCT_FEEDBACK_OPTIONS:
        if canonical.lower() in lower:
            # Extract a short quote around the match
            idx = lower.index(canonical.lower())
            start = max(0, idx - 30)
            end = min(len(feedback_notes), idx + len(canonical) + 30)
            snippet = feedback_notes[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(feedback_notes):
                snippet = snippet + "..."
            found[canonical] = snippet

    # Check keyword variants
    for keyword, canonical in _FEATURE_KEYWORD_MAP.items():
        if canonical not in found and keyword in lower:
            idx = lower.index(keyword)
            start = max(0, idx - 30)
            end = min(len(feedback_notes), idx + len(keyword) + 30)
            snippet = feedback_notes[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(feedback_notes):
                snippet = snippet + "..."
            found[canonical] = snippet

    sentiment = "positive" if outcome == "WON" else "negative"
    return [
        {
            "feature":     name,
            "sentiment":   sentiment,
            "source":      "feedback_notes",
            "loss_causal": True,
            "quote":       quote,
        }
        for name, quote in found.items()
    ]


def _short_feedback_quote(text: str, limit: int = 280) -> str:
    """Return a compact, readable excerpt from customer feedback notes."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    trimmed = clean[:limit].rsplit(" ", 1)[0].strip()
    return trimmed + "..."


def _note_hash(note_objects: list) -> str:
    """Hash sanitized note bodies -- stable across HubSpot re-serialization.

    Accepts both list[dict] (new format with note_id/createdate/body)
    and list[str] (legacy format for cache compatibility).
    """
    bodies = [n["body"] if isinstance(n, dict) else n for n in note_objects]
    return hashlib.sha256("|||".join(bodies).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Notes fetching -- batch associations API with substance gating
# ---------------------------------------------------------------------------

def pull_deal_notes_batch(token: str, deal_ids: list[str]) -> tuple[dict[str, list[dict]], int, int]:
    """Fetch notes for multiple deals using batch association reads.

    Returns (notes_by_deal, skipped_no_notes, skipped_too_short).
    notes_by_deal = {deal_id: [{note_id, createdate, body}, ...]} for qualifying deals.
    """
    if not deal_ids:
        return {}, 0, 0

    # Batch association read: deals -> notes (100 at a time)
    deal_to_note_ids: dict[str, list[str]] = {}
    assoc_url = f"{HUBSPOT_BASE}/crm/v4/associations/deals/notes/batch/read"
    for i in range(0, len(deal_ids), 100):
        batch = deal_ids[i:i + 100]
        assoc_body = {"inputs": [{"id": did} for did in batch]}
        r = requests.post(assoc_url, headers=_hs_headers(token), json=assoc_body, timeout=30)
        if r.status_code not in (200, 207):
            logger.warning("Batch associations failed for chunk %d: %s", i, r.status_code)
            continue
        for result in r.json().get("results", []):
            deal_id = str(result.get("from", {}).get("id", ""))
            note_ids = [str(a.get("toObjectId", "")) for a in result.get("to", [])]
            if deal_id and note_ids:
                deal_to_note_ids[deal_id] = note_ids

    all_note_ids = list({nid for ids in deal_to_note_ids.values() for nid in ids})
    if not all_note_ids:
        # All deals had no associated notes
        return {}, len(deal_ids), 0

    # Batch fetch note bodies (100 at a time)
    note_meta: dict[str, dict] = {}  # nid -> {body, createdate}
    for i in range(0, len(all_note_ids), 100):
        batch = all_note_ids[i:i + 100]
        notes_url = f"{HUBSPOT_BASE}/crm/v3/objects/notes/batch/read"
        notes_body = {
            "inputs": [{"id": nid} for nid in batch],
            "properties": ["hs_note_body", "createdate"],
        }
        nr = requests.post(notes_url, headers=_hs_headers(token), json=notes_body, timeout=30)
        if nr.status_code not in (200, 207):
            logger.warning("Batch notes fetch failed for chunk %d: %s", i, nr.status_code)
            continue
        for note in nr.json().get("results", []):
            nid = str(note.get("id", ""))
            props = note.get("properties", {})
            raw = (props.get("hs_note_body", "") or "")
            clean = _sanitize_note(raw)
            if clean:
                note_meta[nid] = {
                    "body": clean,
                    "createdate": props.get("createdate", ""),
                }

    # Gate on substance
    notes_result: dict[str, list[dict]] = {}
    skipped_no_notes = 0
    skipped_too_short = 0

    # Count deals with no associations at all
    deals_with_assoc = set(deal_to_note_ids.keys())
    deals_without_assoc = len(deal_ids) - len(deals_with_assoc)
    skipped_no_notes += deals_without_assoc

    for deal_id, note_ids in deal_to_note_ids.items():
        note_objects = [
            {"note_id": nid, "createdate": note_meta[nid]["createdate"], "body": note_meta[nid]["body"]}
            for nid in note_ids if nid in note_meta
        ]
        if not note_objects:
            skipped_no_notes += 1
            continue
        combined_text = " ".join(n["body"] for n in note_objects)
        combined_len = sum(len(n["body"]) for n in note_objects)
        if combined_len < MIN_NOTE_CHARS and not _note_has_keyword_override(combined_text):
            skipped_too_short += 1
            logger.debug("Deal %s skipped: %d chars, no keyword override", deal_id, combined_len)
            continue
        notes_result[deal_id] = note_objects

    logger.info(
        "Notes gating: %d qualified, %d skipped (no notes), %d skipped (too short)",
        len(notes_result), skipped_no_notes, skipped_too_short
    )
    return notes_result, skipped_no_notes, skipped_too_short


# ---------------------------------------------------------------------------
# Structured deal property text (prepended before engagement notes)
# ---------------------------------------------------------------------------

def _build_deal_property_text(props: dict) -> str:
    """Build a structured text block from HubSpot deal properties.

    These fields are filled by reps in HubSpot and are more reliable than
    free-text notes when populated. The text is prepended before engagement
    notes so the extraction model sees structured signals first.
    """
    lines = []
    won_notes = (props.get("notes") or "").strip()
    if won_notes:
        lines.append(f"Won Notes (structured): {won_notes}")

    feedback = (props.get("notes_on_customer_feedback") or "").strip()
    if feedback:
        lines.append(f"Customer Feedback (structured): {feedback}")

    product_fb = (props.get("product_feedback") or "").strip()
    if product_fb:
        # Multi-select in HubSpot comes as semicolon-separated values
        lines.append(f"Product Feedback Tags: {product_fb}")

    loss_type = (props.get("loss_type") or "").strip()
    if loss_type:
        lines.append(f"Loss Type (rep-classified): {loss_type}")

    pd_kb = (props.get("kb_or_pd_deal") or "").strip()
    if pd_kb:
        lines.append(f"Deal Type: {pd_kb}")

    uses_comp = (props.get("uses_competitor_platform") or "").strip()
    if uses_comp:
        lines.append(f"Uses Competitor Platform: {uses_comp}")

    comp_platform = (props.get("competitor_platform") or "").strip()
    if comp_platform:
        lines.append(f"Competitor Platform: {comp_platform}")

    if not lines:
        return ""
    return "--- STRUCTURED DEAL PROPERTIES (from HubSpot) ---\n" + "\n".join(lines) + "\n--- END STRUCTURED PROPERTIES ---"


# ---------------------------------------------------------------------------
# AI extraction (Haiku)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Feature extraction: close-time fields only
# ---------------------------------------------------------------------------

FEEDBACK_NOTES_FEATURE_PROMPT = """A sales rep wrote the following notes when marking a deal as {outcome}.
Extract ONLY features explicitly stated as missing, unavailable, or a reason
the deal was lost. Do not extract features Teachable has, features demoed
positively, or features mentioned only as context.

Close notes: {notes}

Return JSON only:
{{
  "features": [
    {{
      "feature": "canonical feature name",
      "quote": "exact phrase from notes, under 20 words"
    }}
  ]
}}

Rules:
- INCLUDE: "they needed X", "missing X", "no X support", "lost because of X",
  "X was a dealbreaker", "X isn't available yet"
- EXCLUDE: "we showed them X", "they liked X", "X works well", anything
  Teachable already has and works
- Use canonical names: "SCORM Support" not "scorm files",
  "Multi-Level Admin Hierarchy (Organizations)" not "org hierarchy",
  "Centralized / Org-Level Reporting" not "reporting"
- If nothing qualifies, return {{"features": []}}
- Return valid JSON only
"""


def extract_features_from_feedback_notes(
    deal_id: str, outcome: str, feedback_notes: str,
    cache: dict, client,
) -> list[dict]:
    """Extract loss-causal features from notes_on_customer_feedback only."""
    cache_key = f"{deal_id}:feedback_features:{hashlib.sha256(feedback_notes.encode()).hexdigest()[:12]}"
    if cache_key in cache:
        return cache[cache_key]

    prompt = FEEDBACK_NOTES_FEATURE_PROMPT.format(
        outcome=outcome, notes=feedback_notes
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        raw_text = response.content[0].text
        if raw_text.strip().startswith("```"):
            raw_text = re.sub(r'^```\w*\n?', '', raw_text.strip())
            raw_text = re.sub(r'\n?```$', '', raw_text.strip())
        features = json.loads(raw_text).get("features", [])
    except (json.JSONDecodeError, IndexError):
        features = []

    sentiment = "positive" if outcome == "WON" else "negative"
    normalized = [
        {
            "feature":     f.get("feature", ""),
            "sentiment":   sentiment,
            "source":      "feedback_notes",
            "loss_causal": True,
            "quote":       f.get("quote"),
        }
        for f in features if f.get("feature")
    ]
    cache[cache_key] = normalized
    return normalized


# ---------------------------------------------------------------------------
# AI extraction: competitors, pricing, outcome only (no features)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are analyzing a closed sales deal. Extract competitive and pricing signals only.
Do NOT extract features. Feature extraction is handled separately.

Deal outcome: {outcome}
Deal amount: {amount}

Close notes (rep's narrative at time of loss):
{feedback_notes}

Supporting timeline notes (context from deal lifecycle):
{supporting_notes}

Return JSON only. No preamble, no markdown:
{{
  "competitors_mentioned": ["exact competitor name"],
  "pricing_signals": [
    {{
      "sentiment": "positive|negative|neutral",
      "reason": "Price too high | Discounting required | Packaging concern | ROI question | Budget constraint | Pricing was fine",
      "quote": "excerpt under 25 words or null"
    }}
  ],
  "loss_outcome_type": "competitive_loss | no_decision | price_budget | product_gap | timing | bad_fit | unknown",
  "win_reason": "one sentence why we won (WON deals only, else null)",
  "loss_reason": "one sentence why we lost (LOST deals only, else null)"
}}

Rules:
- Do NOT include features_mentioned. Omit it entirely
- competitors_mentioned: only if explicitly named. Never infer
- pricing_signals: any mention of cost, budget, pricing concern, discounting, or packaging
- loss_outcome_type: classify the primary reason the deal was lost:
    competitive_loss = prospect chose a named competitor
    no_decision     = prospect went dark, stalled, or chose to do nothing
    price_budget    = primary objection was cost/budget, no specific competitor named
    product_gap     = missing feature was the stated reason, no strong competitor
    timing          = "not now," "maybe next quarter," project deprioritized
    bad_fit         = wrong use case, wrong size, wrong segment
    unknown         = notes don't give enough signal to classify
  (For WON deals, return null)
- If notes are too thin for a field, return an empty array or null. Do not guess
- Return valid JSON only"""


def _build_supporting_notes_text(note_objects: list) -> str:
    """Join engagement note bodies into a single text block."""
    bodies = [n["body"] if isinstance(n, dict) else n for n in note_objects]
    return "\n\n---\n\n".join(bodies)


def extract_deal_signals(deal_id: str, outcome: str, amount: str,
                         feedback_notes: str, supporting_notes: str,
                         cache: dict, client) -> dict:
    """Extract competitor, pricing, and outcome signals. Features not extracted here."""
    combined  = feedback_notes + "|||" + supporting_notes
    note_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]
    cache_key = f"{deal_id}:signals:{note_hash}"

    if cache_key in cache:
        logger.debug("Cache hit: %s", deal_id)
        return cache[cache_key]

    prompt = EXTRACTION_PROMPT.format(
        outcome=outcome,
        amount=f"${float(amount):,.0f}" if amount else "unknown",
        feedback_notes=feedback_notes or "(none)",
        supporting_notes=supporting_notes or "(none)",
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        raw_text = response.content[0].text
        if raw_text.strip().startswith("```"):
            raw_text = re.sub(r'^```\w*\n?', '', raw_text.strip())
            raw_text = re.sub(r'\n?```$', '', raw_text.strip())
        result = json.loads(raw_text)
    except (json.JSONDecodeError, IndexError) as e:
        logger.warning("Extraction parse error for deal %s: %s", deal_id, e)
        result = {
            "competitors_mentioned": [], "pricing_signals": [],
            "loss_outcome_type": "unknown", "win_reason": None, "loss_reason": None,
        }

    result.pop("features_mentioned", None)  # safety
    result["_note_hash"]    = note_hash
    result["_extracted_at"] = datetime.now(UTC).isoformat()
    result["_model"]        = "claude-haiku-4-5-20251001"
    cache[cache_key] = result
    return result


def _has_signal(signals: dict) -> bool:
    """Return True if Haiku returned any useful signal (competitors/pricing/outcome)."""
    return bool(
        signals.get("competitors_mentioned") or
        signals.get("pricing_signals") or
        signals.get("win_reason") or
        signals.get("loss_reason") or
        (signals.get("loss_outcome_type") and signals["loss_outcome_type"] != "unknown")
    )


# ---------------------------------------------------------------------------
# Canonical validation
# ---------------------------------------------------------------------------

def _load_canonical_sets() -> tuple[set, set]:
    """Load canonical feature names and competitor names."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    features = set()
    feature_names_path = os.path.join(base_dir, ".feature_names")
    if os.path.exists(feature_names_path):
        with open(feature_names_path) as f:
            features = {line.strip() for line in f if line.strip()}

    competitors = set()
    competitors_path = os.path.join(base_dir, "competitors.json")
    if os.path.exists(competitors_path):
        with open(competitors_path) as f:
            comps = json.load(f)
        competitors = {c["name"] for c in comps.get("competitors", [])}

    return features, competitors


def _validate_extracted_signals(signals: dict, canonical_features: set,
                                canonical_competitors: set) -> dict:
    """Validate competitor names. Unknown names get NEEDS_REVIEW."""
    validated_comps = []
    for comp in signals.get("competitors_mentioned", []):
        if comp and comp not in canonical_competitors:
            validated_comps.append("NEEDS_REVIEW")
        else:
            validated_comps.append(comp)
    signals["competitors_mentioned"] = validated_comps
    return signals


# ---------------------------------------------------------------------------
# Amount estimation
# ---------------------------------------------------------------------------

def _deal_amount(props: dict, outcome: str) -> tuple[float, bool]:
    """Return (amount, is_estimated). Lost deals with no amount get $6k estimate.

    Safe to apply because: (1) we only fetch from the Sales pipeline where the
    first stage is Discovery, so all deals here had a real evaluation; (2) note
    substance gating excludes deals without meaningful rep notes.
    """
    raw = props.get("amount")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val, False
        except ValueError:
            pass
    if outcome == "LOST":
        return DEFAULT_LOST_AMOUNT, True
    return 0.0, False


# ---------------------------------------------------------------------------
# Aggregation functions (pre-computed for dashboard rendering)
# ---------------------------------------------------------------------------

def _top_n_counter(items: list, n: int) -> list[dict]:
    from collections import Counter
    return [{"name": k, "count": c} for k, c in Counter(items).most_common(n)]


def build_feature_impact_rows(deals: list[dict]) -> list[dict]:
    """Build merged feature rows for the diverging bar chart."""
    from collections import defaultdict
    rows: dict[str, dict] = defaultdict(lambda: {
        "feature": "",
        "won_deal_count": 0, "lost_deal_count": 0,
        "won_known_value": 0.0, "won_estimated_value": 0.0,
        "lost_known_value": 0.0, "lost_estimated_value": 0.0,
        "quote_count": 0, "deal_ids": [],
        "competitors_in_lost_deals": [],
    })

    for deal in deals:
        outcome = deal["outcome"]
        amount = deal["amount"]
        estimated = deal["amount_estimated"]
        deal_id = deal["id"]

        # De-dup features per deal to avoid double-counting value
        seen_features: set[str] = set()
        for feat in deal.get("features_mentioned", []):
            name = feat.get("feature", "")
            if not name or name == "NEEDS_REVIEW":
                continue
            if feat.get("quote"):
                rows[name]["quote_count"] += 1
            if name in seen_features:
                continue
            seen_features.add(name)
            r = rows[name]
            r["feature"] = name
            r["deal_ids"].append(deal_id)
            if outcome == "WON":
                r["won_deal_count"] += 1
                if estimated:
                    r["won_estimated_value"] += amount
                else:
                    r["won_known_value"] += amount
            else:
                r["lost_deal_count"] += 1
                if estimated:
                    r["lost_estimated_value"] += amount
                else:
                    r["lost_known_value"] += amount
                r["competitors_in_lost_deals"].extend(
                    deal.get("competitors_mentioned", [])
                )

    result = []
    for name, r in rows.items():
        r["deal_ids"] = list(set(r["deal_ids"]))
        total_won = r["won_known_value"] + r["won_estimated_value"]
        total_lost = r["lost_known_value"] + r["lost_estimated_value"]
        total_both = total_won + total_lost

        r["associated_value_share"] = round(total_won / total_both, 3) if total_both > 0 else None

        total_est = r["won_estimated_value"] + r["lost_estimated_value"]
        r["estimated_value_share"] = round(total_est / total_both, 3) if total_both > 0 else 0.0

        wc, lc = r["won_deal_count"], r["lost_deal_count"]
        wv, lv = total_won, total_lost
        if wc + lc < 2:
            r["classification"] = "WATCH"
        elif r["estimated_value_share"] > 0.8 and wc + lc < 5:
            r["classification"] = "WATCH"
        elif lv > 0 and wv > 0:
            ratio = min(wv, lv) / max(wv, lv)
            if ratio >= 0.35 and wc >= 2 and lc >= 2:
                r["classification"] = "TABLE_STAKES"
            elif lv > wv:
                r["classification"] = "GAP"
            else:
                r["classification"] = "STRENGTH"
        elif lv > 0:
            r["classification"] = "GAP"
        else:
            r["classification"] = "STRENGTH"

        if r["lost_deal_count"] > 0:
            top_comps = _top_n_counter(r["competitors_in_lost_deals"], 3)
            r["primary_loss_driver"] = "competitive_loss" if top_comps else "product_gap"
            r["top_competitors_in_lost_deals"] = top_comps
        else:
            r["primary_loss_driver"] = None
            r["top_competitors_in_lost_deals"] = []

        del r["competitors_in_lost_deals"]
        result.append(r)

    result.sort(key=lambda r: (
        -r["lost_known_value"],
        -r["lost_estimated_value"],
        -r["lost_deal_count"],
    ))
    return result


def build_competitor_feature_crosswalk(deals: list[dict]) -> list[dict]:
    """For each competitor, features most often cited in the same lost deals."""
    from collections import defaultdict, Counter
    comp_to_features: dict[str, Counter] = defaultdict(Counter)
    comp_to_value: dict[str, float] = defaultdict(float)
    comp_to_count: dict[str, int] = defaultdict(int)

    for deal in deals:
        if deal["outcome"] != "LOST":
            continue
        comps = deal.get("competitors_mentioned", [])
        feats = [f["feature"] for f in deal.get("features_mentioned", [])
                 if f.get("feature") and f["feature"] != "NEEDS_REVIEW"]
        amount = deal["amount"]
        for comp in comps:
            if not comp or comp == "NEEDS_REVIEW":
                continue
            comp_to_count[comp] += 1
            comp_to_value[comp] += amount
            for feat in feats:
                comp_to_features[comp][feat] += 1

    rows = []
    for comp, feat_counter in comp_to_features.items():
        rows.append({
            "competitor": comp,
            "lost_deal_count": comp_to_count[comp],
            "lost_associated_value": comp_to_value[comp],
            "top_features_in_lost_deals": [
                {"feature": f, "count": c}
                for f, c in feat_counter.most_common(5)
            ],
        })
    rows.sort(key=lambda r: -r["lost_deal_count"])
    return rows


def build_loss_outcome_summary(deals: list[dict]) -> dict:
    """Distribution of loss_outcome_type across lost deals."""
    from collections import Counter
    counter = Counter(
        d.get("loss_outcome_type", "unknown")
        for d in deals if d["outcome"] == "LOST"
    )
    total_lost = sum(counter.values())
    return {
        "total_lost_analyzed": total_lost,
        "by_type": [
            {"type": t, "count": c, "share": round(c / total_lost, 3) if total_lost else 0}
            for t, c in counter.most_common()
        ],
    }


def build_pricing_rows(deals: list[dict]) -> list[dict]:
    """Aggregate pricing signals by reason category."""
    from collections import defaultdict
    groups: dict[str, dict] = defaultdict(lambda: {
        "reason": "", "won_count": 0, "lost_count": 0,
        "associated_value": 0.0, "quotes": [],
    })
    for deal in deals:
        for ps in deal.get("pricing_signals", []):
            reason = ps.get("reason", "Other") if isinstance(ps, dict) else "Other"
            g = groups[reason]
            g["reason"] = reason
            if deal["outcome"] == "WON":
                g["won_count"] += 1
            else:
                g["lost_count"] += 1
            g["associated_value"] += deal["amount"]
            quote = ps.get("quote", "") if isinstance(ps, dict) else ""
            if quote:
                g["quotes"].append({
                    "text": quote,
                    "outcome": deal["outcome"],
                    "deal": deal["name"],
                })
    result = list(groups.values())
    result.sort(key=lambda r: -(r["won_count"] + r["lost_count"]))
    for r in result:
        r["quotes"] = r["quotes"][:3]
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch Win/Loss deal data from HubSpot")
    parser.add_argument("--days", type=int, default=180, help="Lookback window (default 180)")
    parser.add_argument("--out", default="test_output/win_loss.json", help="Output path")
    parser.add_argument("--pipeline", default="Sales", help="Pipeline label to filter (default 'Sales')")
    parser.add_argument("--dry-run", action="store_true", help="Fetch deals but skip extraction")
    parser.add_argument("--write-dry-run", action="store_true",
                        help="Force writing output even on dry-run (defaults to .dry_run.json)")
    parser.add_argument("--dump-notes", default=None, metavar="PATH", nargs="?",
                        const=".win_loss_private/deal_notes_dump.json",
                        help="Export deals + note bodies to JSON for manual extraction, then exit (default: .win_loss_private/)")
    parser.add_argument("--signals-file", default=None, metavar="PATH",
                        help="Import pre-extracted signals from JSON (skips API extraction)")
    parser.add_argument("--api-extract", action="store_true",
                        help="Also use Anthropic API for AI extraction from feedback notes (optional, requires ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    # Default mode: HubSpot structured fields only (no Anthropic key needed).
    # --api-extract optionally enhances with AI extraction from feedback notes.
    # Auto-detect: if ANTHROPIC_API_KEY is set and no other mode specified, enable AI extraction
    if not args.dry_run and args.dump_notes is None and args.signals_file is None and not args.api_extract:
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            args.api_extract = True
            logger.info("Auto-detected ANTHROPIC_API_KEY, enabling --api-extract for enhanced extraction")

    # Dry-run safety: don't overwrite production data with empty signals
    if args.dry_run:
        prod_path = "test_output/win_loss.json"
        if args.out == prod_path and not args.write_dry_run:
            args.out = prod_path.replace(".json", ".dry_run.json")
            logger.info("Dry-run: redirecting output to %s (use --write-dry-run to override)", args.out)

    token = os.getenv("HUBSPOT_TOKEN", "")
    if not token:
        raise SystemExit("ERROR: HUBSPOT_TOKEN not set")

    # Step 1: Get pipeline stages
    stage_map, pipelines = pull_hubspot_pipeline_stages(token)
    outcome_ids = get_outcome_stage_ids(stage_map, pipelines, args.pipeline)
    won_stage_set = set(outcome_ids['WON'])
    lost_stage_set = set(outcome_ids['LOST'])
    logger.info("WON stages: %s, LOST stages: %s",
                [stage_map.get(s, s) for s in outcome_ids['WON']],
                [stage_map.get(s, s) for s in outcome_ids['LOST']])

    if not outcome_ids['WON'] and not outcome_ids['LOST']:
        raise SystemExit(f"ERROR: No WON/LOST stages found in pipeline '{args.pipeline}'")

    # Step 2: Fetch owners
    owners = pull_hubspot_owners(token)

    # Step 3: Fetch closed deals
    since = datetime.now(UTC) - timedelta(days=args.days)
    raw_deals = pull_closed_deals(token, since, outcome_ids)
    logger.info("Fetched %d closed deals", len(raw_deals))

    # Classify outcomes
    # Exclude all deals owned by excluded reps (non-sales roles, skew the data)
    EXCLUDE_OWNER_EMAILS = {"jerome.olaloye@teachable.com"}
    EXCLUDE_DEAL_NAMES = {"edge factor"}  # outlier accounts that skew the data
    ALLOWED_LEAD_SOURCES = {"inbound", "outbound", "referral"}

    classified_deals = []
    closed_won_fetched = 0
    closed_lost_fetched = 0
    excluded_by_owner = 0
    excluded_by_lead_source = 0
    for raw_deal in raw_deals:
        props = raw_deal.get("properties", {})
        stage_id = props.get("dealstage", "")
        if stage_id in won_stage_set:
            outcome = "WON"
        elif stage_id in lost_stage_set:
            outcome = "LOST"
        else:
            continue
        # Exclude specific deal names (case-insensitive)
        deal_name = (props.get("dealname") or "").strip()
        if deal_name.lower() in EXCLUDE_DEAL_NAMES:
            continue

        product_limitation_scope_override = (
            outcome == "LOST"
            and (props.get("loss_reason") or "").strip().lower() == "product limitation"
        )

        # Filter by lead source (only inbound, outbound, referral), but never
        # drop a HubSpot-marked Product Limitation loss. Those are the source
        # of truth for product-gap coverage.
        lead_source = (props.get("lead_source") or "").strip().lower()
        if lead_source and lead_source not in ALLOWED_LEAD_SOURCES and not product_limitation_scope_override:
            excluded_by_lead_source += 1
            continue

        # Check owner exclusion by email (deterministic, no name-parsing fragility),
        # again preserving any Product Limitation loss.
        owner_id = props.get("hubspot_owner_id", "")
        owner_info = owners.get(owner_id, {})
        owner_email = (owner_info.get("email") or "").lower()
        owner_name = owner_info.get("name", "")
        if owner_email in EXCLUDE_OWNER_EMAILS and not product_limitation_scope_override:
            logger.debug("Excluding deal owned by %s (%s)", owner_name, owner_email)
            excluded_by_owner += 1
            continue

        if outcome == "WON":
            closed_won_fetched += 1
        else:
            closed_lost_fetched += 1
        classified_deals.append((raw_deal, outcome))

    if excluded_by_lead_source:
        logger.info("Excluded %d deals (lead source not in %s)", excluded_by_lead_source, ALLOWED_LEAD_SOURCES)
    if excluded_by_owner:
        logger.info("Excluded %d deals (owner filter: %s)", excluded_by_owner, EXCLUDE_OWNER_EMAILS)

    logger.info("Classified: %d won, %d lost", closed_won_fetched, closed_lost_fetched)

    # Step 4: Fetch notes in batch with substance gating
    deal_ids = [str(d.get("id", "")) for d, _ in classified_deals]
    deal_notes, skipped_no_notes, skipped_too_short = pull_deal_notes_batch(token, deal_ids)

    # --dump-notes: export for manual extraction and exit
    if args.dump_notes:
        dump = []
        for raw_deal, outcome in classified_deals:
            did = str(raw_deal.get("id", ""))
            notes = deal_notes.get(did)
            if not notes:
                continue
            props = raw_deal.get("properties", {})
            amount, is_estimated = _deal_amount(props, outcome)
            deal_prop_text = _build_deal_property_text(props)
            dump.append({
                "deal_id": did,
                "name": props.get("dealname", ""),
                "outcome": outcome,
                "amount": amount,
                "amount_estimated": is_estimated,
                "close_lost_reason": props.get("hs_closed_lost_reason"),
                "deal_property_text": deal_prop_text,
                "structured_properties": {
                    "notes": props.get("notes"),
                    "notes_on_customer_feedback": props.get("notes_on_customer_feedback"),
                    "product_feedback": props.get("product_feedback"),
                    "loss_reason": props.get("loss_reason"),
                    "loss_type": props.get("loss_type"),
                    "kb_or_pd_deal": props.get("kb_or_pd_deal"),
                    "uses_competitor_platform": props.get("uses_competitor_platform"),
                    "competitor_platform": props.get("competitor_platform"),
                },
                "notes": [{"note_id": n["note_id"], "createdate": n["createdate"], "body": n["body"]}
                          for n in notes],
            })
        os.makedirs(os.path.dirname(args.dump_notes) or '.', exist_ok=True)
        with open(args.dump_notes, "w") as f:
            json.dump(dump, f, indent=2)
        print(f"Dumped {len(dump)} deals with notes to {args.dump_notes}")
        return

    # Step 5: Process each deal
    deals_output = []
    feature_signals = {"WON": {}, "LOST": {}}
    competitor_signals = {"WON": {}, "LOST": {}}
    deals_no_signal = 0
    analyzed_won = 0
    analyzed_lost = 0

    # Separate fetched-wide vs analyzed-only value totals (brief item #1)
    fetched_value = {"won_known": 0.0, "lost_known": 0.0, "lost_estimated": 0.0}
    analyzed_value = {"won_known": 0.0, "lost_known": 0.0, "lost_estimated": 0.0}

    # Pre-compute amounts for all classified deals (fetched totals)
    deal_amounts: dict[str, tuple[float, bool]] = {}
    for raw_deal, outcome in classified_deals:
        did = str(raw_deal.get("id", ""))
        props = raw_deal.get("properties", {})
        amount, is_estimated = _deal_amount(props, outcome)
        deal_amounts[did] = (amount, is_estimated)
        if outcome == "WON":
            fetched_value["won_known"] += amount if not is_estimated else 0
        else:
            if is_estimated:
                fetched_value["lost_estimated"] += amount
            else:
                fetched_value["lost_known"] += amount

    for raw_deal, outcome in classified_deals:
        did = str(raw_deal.get("id", ""))
        props = raw_deal.get("properties", {})
        amount, is_estimated = deal_amounts[did]

        # Substance gate: outcome-specific close notes OR substantive engagement notes.
        # Closed-won deals use the HubSpot deal property named "notes"; closed-lost
        # product feedback uses "notes_on_customer_feedback".
        close_won_notes = _sanitize_note(props.get("notes") or "")
        feedback_notes = _sanitize_note(props.get("notes_on_customer_feedback") or "")
        primary_close_notes = close_won_notes if outcome == "WON" else feedback_notes
        primary_close_notes_field = "notes" if outcome == "WON" else "notes_on_customer_feedback"
        eng_notes = deal_notes.get(did, [])
        supporting_text = _build_supporting_notes_text(eng_notes) if eng_notes else ""
        combined_text = primary_close_notes + " " + supporting_text
        hubspot_loss_reason = (props.get("loss_reason") or "").strip()
        product_limitation_marked = (
            outcome == "LOST"
            and hubspot_loss_reason.lower() == "product limitation"
        )

        if not product_limitation_marked \
                and len(combined_text.strip()) < MIN_NOTE_CHARS \
                and not _note_has_keyword_override(combined_text):
            continue

        # Owner email
        owner_id = props.get("hubspot_owner_id", "")
        rep_email = owners.get(owner_id, {}).get("email", "")

        # Close date
        closedate_raw = props.get("closedate", "")
        closedate = ""
        if closedate_raw:
            try:
                closedate = datetime.fromisoformat(closedate_raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                closedate = closedate_raw[:10] if len(closedate_raw) >= 10 else closedate_raw

        product_feedback_raw = (props.get("product_feedback") or "").strip()
        product_feedback_markers = _parse_product_feedback(product_feedback_raw)

        # ── Feature signals: won deals use won notes; lost feature gaps are
        # marker-first and only use notes after HubSpot says Product Limitation.
        if outcome == "WON" or product_limitation_marked:
            combined_features = _extract_features_from_notes(primary_close_notes, outcome)
        else:
            combined_features = []
        if product_limitation_marked and not combined_features:
            combined_features = [{
                "feature": UNCLASSIFIED_PRODUCT_LIMITATION_FEATURE,
                "sentiment": "negative",
                "source": "feedback_notes",
                "loss_causal": True,
                "quote": _short_feedback_quote(feedback_notes) or "No customer feedback note captured.",
                "needs_review": True,
            }]

        # ── Competitor / loss type from structured HubSpot fields ────────
        comp_platform = (props.get("competitor_platform") or "").strip()
        competitors_mentioned = [comp_platform] if comp_platform else []
        loss_type_raw = (props.get("loss_type") or "").strip().lower().replace(" ", "_")
        if product_limitation_marked:
            loss_outcome_type = "product_gap"
        else:
            loss_outcome_type = loss_type_raw if loss_type_raw else ("unknown" if outcome == "LOST" else None)

        if outcome == "WON":
            analyzed_won += 1
            analyzed_value["won_known"] += amount if not is_estimated else 0
        else:
            analyzed_lost += 1
            if is_estimated:
                analyzed_value["lost_estimated"] += amount
            else:
                analyzed_value["lost_known"] += amount

        # Accumulate feature signals (from close-time fields only)
        for feat in combined_features:
            fname = feat.get("feature", "")
            if not fname:
                continue
            if fname not in feature_signals[outcome]:
                feature_signals[outcome][fname] = {
                    "feature": fname,
                    "mention_count": 0,
                    "associated_deal_value": 0.0,
                    "known_value": 0.0,
                    "estimated_value": 0.0,
                    "estimated_deal_count": 0,
                    "deal_ids": [],
                }
            fs = feature_signals[outcome][fname]
            fs["mention_count"] += 1
            fs["associated_deal_value"] += amount
            if is_estimated:
                fs["estimated_value"] += amount
                fs["estimated_deal_count"] += 1
            else:
                fs["known_value"] += amount
            if did not in fs["deal_ids"]:
                fs["deal_ids"].append(did)

        # Accumulate competitor signals (from structured HubSpot fields)
        for comp in competitors_mentioned:
            if not comp:
                continue
            if comp not in competitor_signals[outcome]:
                competitor_signals[outcome][comp] = {
                    "competitor": comp,
                    "count": 0,
                    "associated_deal_value": 0.0,
                    "known_value": 0.0,
                    "estimated_value": 0.0,
                }
            cs = competitor_signals[outcome][comp]
            cs["count"] += 1
            cs["associated_deal_value"] += amount
            if is_estimated:
                cs["estimated_value"] += amount
            else:
                cs["known_value"] += amount

        deals_output.append({
            "id": did,
            "name": props.get("dealname", ""),
            "outcome": outcome,
            "amount": amount,
            "amount_estimated": is_estimated,
            "closedate": closedate,
            "pipeline": args.pipeline,
            "rep_email": rep_email,
            "lead_source": props.get("lead_source"),
            "close_lost_reason_field": props.get("hs_closed_lost_reason"),
            "kb_or_pd_deal": props.get("kb_or_pd_deal"),
            "hubspot_loss_reason": hubspot_loss_reason,
            "loss_type": props.get("loss_type"),
            "product_limitation_marked": product_limitation_marked,
            "product_feedback_marker_count": len(product_feedback_markers),
            "competitor_platform": props.get("competitor_platform"),
            "close_won_notes": close_won_notes,
            "notes_on_customer_feedback": feedback_notes,
            "primary_close_notes": primary_close_notes,
            "primary_close_notes_field": primary_close_notes_field,
            "features_mentioned": combined_features,
            "competitors_mentioned": competitors_mentioned,
            "pricing_signals": [],
            "loss_outcome_type": loss_outcome_type,
            "win_reason": None,
            "loss_reason": None,
            "notes": [{"note_id": n["note_id"], "createdate": n["createdate"]}
                      for n in eng_notes] if eng_notes and isinstance(eng_notes[0], dict) else [],
            "notes_count": len(eng_notes),
        })

    # Build summary
    deals_with_substantive_notes = len(deal_notes)
    has_signal = bool(
        any(feature_signals[k] for k in feature_signals) or
        any(competitor_signals[k] for k in competitor_signals)
    )
    extraction_status = "dry_run" if args.dry_run else "structured_fields"

    summary = {
        "closed_won_fetched": closed_won_fetched,
        "closed_lost_fetched": closed_lost_fetched,
        "analyzed_won_count": analyzed_won,
        "analyzed_lost_count": analyzed_lost,
        "deals_with_substantive_notes": deals_with_substantive_notes,
        "deals_skipped_no_notes": skipped_no_notes,
        "deals_skipped_notes_too_short": skipped_too_short,
        "deals_with_notes_no_signal": deals_no_signal,
        "excluded_by_owner": excluded_by_owner,
        "min_note_chars_threshold": MIN_NOTE_CHARS,
        "fetched_value": fetched_value,
        "analyzed_value": analyzed_value,
        "estimate_basis": "$6,000 assumed for lost deals with no amount set (Teachable base enterprise cost)",
    }

    # Pre-aggregated output arrays (computed in Python, rendered in JS)
    fi_rows = build_feature_impact_rows(deals_output)
    crosswalk = build_competitor_feature_crosswalk(deals_output)
    loss_outcomes = build_loss_outcome_summary(deals_output)
    pricing_rows = build_pricing_rows(deals_output)

    has_signal = bool(fi_rows or crosswalk)

    # Build output
    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": args.days,
        "pipeline": args.pipeline,
        "hubspot_portal_id": "50445500",
        "extraction_model": "local_keyword_match",
        "extraction_status": extraction_status,
        "has_signal_data": has_signal,
        "dry_run": args.dry_run,
        "summary": summary,
        "feature_impact_rows": fi_rows,
        "competitor_feature_crosswalk": crosswalk,
        "loss_outcome_summary": loss_outcomes,
        "pricing_packaging_rows": pricing_rows,
        "deals": deals_output,
        "feature_signals": {
            "WON": sorted(feature_signals["WON"].values(),
                          key=lambda x: x["known_value"], reverse=True),
            "LOST": sorted(feature_signals["LOST"].values(),
                           key=lambda x: (x["known_value"], x["estimated_value"]),
                           reverse=True),
        },
        "competitor_signals": {
            "WON": sorted(competitor_signals["WON"].values(),
                          key=lambda x: x["associated_deal_value"], reverse=True),
            "LOST": sorted(competitor_signals["LOST"].values(),
                           key=lambda x: x["associated_deal_value"], reverse=True),
        },
    }

    # Write output
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)
    logger.info("Wrote %s (%d deals)", args.out, len(deals_output))

    # Print summary
    total_fetched = closed_won_fetched + closed_lost_fetched
    total_analyzed = analyzed_won + analyzed_lost
    print(f"\nWin/Loss extraction complete:")
    print(f"  {args.pipeline} pipeline. Last {args.days} days")
    print(f"  {total_fetched} closed deals fetched ({closed_won_fetched} won. {closed_lost_fetched} lost)")
    print(f"  {deals_with_substantive_notes} had substantive notes")
    if skipped_no_notes:
        print(f"    {skipped_no_notes} skipped: no HubSpot note body")
    if skipped_too_short:
        print(f"    {skipped_too_short} skipped: notes too short + no keyword signal")
    if deals_no_signal:
        print(f"  {deals_no_signal} extracted but returned no signal from Haiku")
    print(f"  {total_analyzed} deals stored in win_loss.json ({analyzed_won} won. {analyzed_lost} lost)")
    print(f"  Output: {args.out}")

    if feature_signals['WON'] or feature_signals['LOST']:
        print(f"  Features: WON={len(feature_signals['WON'])} LOST={len(feature_signals['LOST'])}")
    if competitor_signals['WON'] or competitor_signals['LOST']:
        print(f"  Competitors: WON={len(competitor_signals['WON'])} LOST={len(competitor_signals['LOST'])}")

    # Flag NEEDS_REVIEW
    nr_features = sum(1 for s in feature_signals.values() for f in s.values() if f["feature"] == "NEEDS_REVIEW")
    nr_comps = sum(1 for s in competitor_signals.values() for c in s.values() if c["competitor"] == "NEEDS_REVIEW")
    if nr_features or nr_comps:
        print(f"  NEEDS_REVIEW: {nr_features} features, {nr_comps} competitors")


if __name__ == "__main__":
    main()
