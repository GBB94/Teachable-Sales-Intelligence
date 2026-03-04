#!/usr/bin/env python3
"""
AI feature analysis helper for Claude Code workflow.

Three-step process:
  1. `extract`   — reads a dashboard HTML file, prints call transcripts for review
  2. `normalize` — merges similar feature names in a features JSON file
  3. `inject`    — takes a features JSON file, rewrites the dashboard + HubSpot notes

Usage (inside Claude Code):
  python3 analyze_features.py extract test_output/index.html [--prior old_dashboard.html]
  # ... Claude Code reads transcripts, builds features.json ...
  python3 analyze_features.py normalize features.json --merge-map merge.json
  python3 analyze_features.py inject test_output/index.html features.json [--notes test_output/notes.txt]
"""

import argparse
from datetime import date
from difflib import get_close_matches
import hashlib
import json
import os
import re
import sys


CANONICAL_NAMES_FILE = ".feature_names"

# Internal Teachable employees — never the "source" of a feature request
INTERNAL_SPEAKER_NAMES = {
    'zach mccall', 'kevin', 'kevin codde', 'jerome', 'jerome olaloye',
    'lennie zhu', 'sarah dean',
    'jonathan corvin-blackburn', 'jonathan corvin blackburn',
}


def _is_internal_speaker(speaker: str) -> bool:
    """Check if a speaker is an internal Teachable employee."""
    if not speaker:
        return True
    low = speaker.lower().strip()
    if '(teachable)' in low or 'teachable.com' in low:
        return True
    name = low.split('(')[0].strip()
    return name in INTERNAL_SPEAKER_NAMES


def _infer_company_from_title(title: str) -> str:
    """Extract prospect company name from a call title."""
    if '<>' in title:
        parts = title.split('<>')
        for part in parts:
            cleaned = part.strip().split(':')[0].strip()
            if cleaned.lower() not in ('teachable', ''):
                for suffix in (' Followup', ' Follow-up', ' Follow Up'):
                    if cleaned.endswith(suffix):
                        cleaned = cleaned[:-len(suffix)].strip()
                return cleaned
    return ""


def _infer_company_from_call(call: dict) -> str:
    """Infer the prospect company from all available call data."""
    # 1. Title extraction (e.g. "Teachable <> BADM" → "BADM")
    company = _infer_company_from_title(call.get("title", ""))
    if company:
        return company

    # 2. marketing_data.company (e.g. "ESI (Eating Smart International)" → "ESI")
    md = call.get("marketing_data")
    if md and md.get("company"):
        raw = md["company"]
        if '(' in raw:
            before = raw.split('(')[0].strip()
            if before:
                return before
        return raw

    # 3. Attendees — first non-internal attendee
    for att in (call.get("attendees") or "").split(","):
        att = att.strip()
        if att and not _is_internal_speaker(att):
            if '(' in att:
                return att.split('(')[1].rstrip(')')
            return att

    return ""


def generate_mention_id(call_id: str, feature_name: str) -> str:
    """Generate a stable, deterministic ID for a mention."""
    raw = f"{call_id}|{feature_name}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def write_canonical_json(data: dict, output_dir: str):
    """Write the canonical features.json that all downstream tools read from."""
    path = os.path.join(output_dir, "features.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote canonical data to {path}")


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

    # The data is on a line like: let DATA = {...}; or const DATA = {...};
    match = re.search(r"(?:let|const) DATA = ({.*?});\s*$", html, re.MULTILINE | re.DOTALL)
    if not match:
        print("Error: Could not find DATA JSON in dashboard HTML.")
        sys.exit(1)

    # Find the actual marker used (let or const)
    marker_match = re.search(r"(let|const) DATA = ", html)
    start_marker = marker_match.group(0)
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

    # Replace the let/const DATA = ...; line
    marker_match = re.search(r"(let|const) DATA = ", html)
    start_marker = marker_match.group(0)
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
    """Extract and print call transcripts from dashboard HTML.

    By default only extracts calls with pending_analysis=true.
    Use --all to extract every call.
    """
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
    all_calls = data.get("calls", [])

    # Determine which calls already have features extracted
    analyzed_ids = {m.get("call_id") for m in data.get("mentions", [])}

    if args.all:
        calls = all_calls
        print(f"Found {len(calls)} total calls in dashboard.\n")
    else:
        # Only extract calls that are pending analysis
        calls = [c for c in all_calls if c.get("pending_analysis") or c.get("id") not in analyzed_ids]
        pending_count = len([c for c in all_calls if c.get("pending_analysis")])
        unanalyzed_count = len(calls) - pending_count
        print(f"Found {len(all_calls)} calls total, {len(calls)} need analysis "
              f"({pending_count} pending, {unanalyzed_count} unanalyzed).\n")
        if not calls:
            print("All calls have been analyzed. Use --all to re-extract everything.")
            return

    for i, call in enumerate(calls, 1):
        transcript = call.get("transcript_text", "")
        word_count = len(transcript.split()) if transcript else 0

        status = ""
        if call.get("pending_analysis"):
            status = " [PENDING]"
        elif call.get("id") not in analyzed_ids:
            status = " [UNANALYZED]"

        print(f"{'='*70}")
        print(f"[{i}] {call['title']}{status}")
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

    # Print analysis instructions and expected output format for CC
    print(f"{'='*70}")
    print("DATA SOURCE")
    print(f"{'─'*70}")
    print("""The transcripts above are already saved in the dashboard data. Each call
has a transcript_text field with the full transcript. Do NOT re-pull from
the Fireflies API. Just read the transcripts printed above (or from
test_output/calls_for_analysis.json).

Only pull from Fireflies when scanning for NEW calls that aren't already
in the data (via the server scan UI or CLI).
""")

    print(f"{'='*70}")
    print("ANALYSIS PROMPT")
    print(f"{'─'*70}")
    print("""Read each sales call transcript carefully IN ITS ENTIRETY. Identify every
product feature, capability, or topic discussed in the context of what the
customer needs, is evaluating, or is interested in.

Include:
- Features the customer explicitly asks for ("we need X")
- Features the customer asks questions about ("how do your quizzes work?")
- Features discussed as part of the customer's use case or requirements
- Existing features the customer wants customized or improved
- Features the rep demos or pitches that the customer engages with
- Topics referenced in the call title or meeting agenda
- Capabilities the customer compares to their current/competing platform
- Pain points that imply a missing feature ("it's so manual", "we can't do X")

Do NOT include:
- Small talk, scheduling, or logistics
- Generic platform questions ("how much does it cost?") unless tied to a
  specific feature
- Internal Teachable discussion not relevant to a product capability

Be thorough. If in doubt, include it. A shallow analysis that misses
features discussed on the call is worse than a slightly long list.

SPEAKER & COMPANY RULES:
- ONLY extract features said by PROSPECT speakers. Never extract anything
  said by a Teachable employee. The sales rep (Zach McCall) is on every call
  but must NEVER appear in the output — not as a speaker, contact, or source.
- Internal Teachable employees: anyone @teachable.com, Zach McCall, Kevin,
  Jerome, Lennie Zhu, Sarah Dean, Jonathan Corvin-Blackburn. Skip anything
  these speakers say, even if they pitch or demo a feature.
- If a prospect asks about or responds to something the rep pitches, attribute
  the feature to the PROSPECT speaker who expressed the need, not to the rep.
- Every feature MUST have a "company" field with the PROSPECT company name.
  Never use "Unknown", "Teachable", or empty string.
- Infer the company from the call title if not obvious from the speaker.
  Example: "Teachable <> Dot Compliance Followup" → company is "Dot Compliance"
  Example: "Teachable <> Speravita: Organizations Review" → company is "Speravita"
- The "speaker" field must always be a PROSPECT name: "Ibrahim Haleem Khan (Dot Compliance)"
""")

    # Load canonical category and segment names from JSON files
    base_dir = os.path.dirname(os.path.abspath(__file__))
    categories_path = os.path.join(base_dir, "categories.json")
    segments_path = os.path.join(base_dir, "segments.json")

    cat_names = []
    if os.path.exists(categories_path):
        with open(categories_path, "r") as f:
            cats = json.load(f)
        cat_names = [c["name"] for c in cats.get("categories", [])]

    seg_names = []
    if os.path.exists(segments_path):
        with open(segments_path, "r") as f:
            segs = json.load(f)
        seg_names = [s["name"] for s in segs.get("segments", [])]

    # Print exact canonical names as numbered lists
    print(f"{'='*70}")
    print("FEATURE CATEGORIES (use these EXACT names, no variations):")
    print(f"{'─'*70}")
    for i, name in enumerate(cat_names, 1):
        cat_obj = cats["categories"][i - 1]
        examples = ", ".join(cat_obj.get("examples", [])[:4])
        print(f"  {i}. {name}")
        print(f"     {cat_obj['description']}")
        print(f"     e.g. {examples}")
    print()

    print(f"{'='*70}")
    print("PERSONA SEGMENTS (use these EXACT names, no variations):")
    print(f"{'─'*70}")
    for i, name in enumerate(seg_names, 1):
        seg_obj = segs["segments"][i - 1]
        examples = ", ".join(seg_obj.get("examples", [])[:3])
        signals = ", ".join(seg_obj.get("signals", [])[:4])
        print(f"  {i}. {name}")
        print(f"     {seg_obj['description']}")
        print(f"     e.g. {examples} | signals: {signals}")
    print()

    # Load and print competitors for extraction
    comp_names = []
    competitors_path = os.path.join(base_dir, "competitors.json")
    if os.path.exists(competitors_path):
        with open(competitors_path, "r") as f:
            comps = json.load(f)
        comp_names = [c["name"] for c in comps.get("competitors", [])]
        comp_types = comps.get("competitor_types", {})

        print(f"{'='*70}")
        print("COMPETITORS (use these EXACT names, no variations):")
        print(f"{'─'*70}")
        by_type = {}
        for c in comps.get("competitors", []):
            t = c.get("type", "other")
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(c)
        for t_key, t_desc in comp_types.items():
            group = by_type.get(t_key, [])
            if group:
                print(f"\n  {t_key.upper()} — {t_desc}")
                for c in group:
                    print(f"    - {c['name']}: {c['description']}")
        print()

    print(f"{'='*70}")
    print("COMPETITOR EXTRACTION")
    print(f"{'─'*70}")
    print("""Scan the transcript for any mentions of competing platforms, LMS systems,
or alternative solutions the prospect has used, evaluated, or asked about.

Use ONLY competitor names from the list above. Do NOT create new names.
If a competitor is mentioned that is not on the list, use "NEEDS_REVIEW"
as the competitor name and include what was actually said in the context.

For each competitor mention, capture:
- competitor: Exact name from the list
- context: 1-3 sentence summary of what was said about this competitor.
  Written from the prospect's perspective. Not a quote, a summary.
- timestamp: Approximate location in the transcript (MM:SS)
- mention_type: One of:
    "currently_using"  — prospect is actively on this platform today
    "switching_from"   — prospect is leaving or has left this platform
    "evaluated"        — prospect looked at it but didn't choose it
    "asked_about"      — prospect asked how Teachable compares
    "compared_to"      — prospect or rep compared specific features/pricing

Do NOT count:
- Mentions by the Teachable sales rep (only prospect mentions)
- Generic references to "other platforms" without naming a specific competitor
- Mentions of tools that aren't competing with Teachable (e.g., Zoom, Stripe, Zapier)

If no competitors were mentioned, return an empty array: "competitor_mentions": []
""")

    print(f"{'='*70}")
    print("CRITICAL RULES")
    print(f"{'─'*70}")
    print("""- You MUST use category, segment, and competitor names EXACTLY as listed above.
- Do NOT create new categories, segments, or competitor names. Do NOT abbreviate. Do NOT rephrase.
- If a feature does not clearly fit any category, set category to "NEEDS_REVIEW".
- If a prospect does not clearly fit any segment, set segment to "NEEDS_REVIEW".
- If a competitor is mentioned that is not on the list, set competitor to "NEEDS_REVIEW".
  Also set "suggested_category", "suggested_new_segment", or include the actual name in context.
- "NEEDS_REVIEW" items will be flagged for manual assignment.
- NEVER use "Other" as a category or segment name.
- For INTERNAL calls, set all segment fields to null and competitor_mentions to [].
""")

    print(f"{'='*70}")
    print("OUTPUT FORMAT")
    print(f"{'─'*70}")
    print("Write a JSON file with this structure:")
    print("""
{
  "features": {
    "<call_id>": [
      {
        "feature": "Short Normalized Feature Name",
        "category": "EXACT Category Name from list above (or NEEDS_REVIEW)",
        "suggested_category": "only if NEEDS_REVIEW — what you would have named it",
        "company": "Prospect Company Name (NEVER 'Teachable' or 'Unknown')",
        "speaker": "Customer Name (Company)",
        "quote": "most relevant 1-2 sentence verbatim quote",
        "timestamp": "~MM:SS",
        "ts_seconds": 123,
        "type": "prospect_request | prospect_interest"
      }
    ]
  },
  "notes": {
    "<call_id>": "full HubSpot note text (see format below)"
  },
  "marketing_data": {
    "<call_id>": { ... per-call marketing intelligence (see MARKETING section) ... }
  },
  "segment_data": {
    "<call_id>": {
      "segment": "EXACT segment name from list above (or NEEDS_REVIEW)",
      "segment_confidence": 0.85,
      "segment_reasoning": "One sentence explaining why this segment fits",
      "alternative_segment": "Second-best fit or null",
      "suggested_new_segment": "only if NEEDS_REVIEW — what you would have named it, else null"
    }
  },
  "competitor_mentions": {
    "<call_id>": [
      {
        "competitor": "EXACT name from competitors list (or NEEDS_REVIEW)",
        "context": "1-3 sentence summary of what was said about this competitor",
        "timestamp": "MM:SS",
        "mention_type": "currently_using|switching_from|evaluated|asked_about|compared_to"
      }
    ]
  },
  "recap": "optional weekly recap paragraph",
  "company_summaries": { "Company": "one-line summary" }
}

Feature type meanings:
  "prospect_request"  — customer explicitly asked for this feature
  "prospect_interest" — customer asked about it or engaged positively
Do NOT use "rep_highlighted". Only extract features from prospect speakers.
""")
    print("HUBSPOT NOTE FORMAT (for the 'notes' field):")
    print(f"{'─'*70}")
    print("""CALL DATE: YYYY-MM-DD
ATTENDEES: Name (email), Name (email)
COMPANY: Company Name
STAGE: Discovery / Demo / Followup / Negotiation / Closed

---
SUMMARY
2-3 sentences: who they are, what they want to build, where they are in evaluation

---
USE CASE
Primary goal:
Audience:
Business model:
Content types:
Scale expectations:

---
QUALIFICATION
Budget:
Authority:
Need:
Timeline:

---
TECHNICAL REQUIREMENTS
Integrations:
Reporting needs:
Payments / checkout:
Admin or seat management:
Special workflows or constraints:

---
BUYING SIGNALS
- specific signal observed

---
RISKS / OBJECTIONS
- specific risk

---
PRODUCT FEEDBACK
- specific feedback

---
PRICING DISCUSSED
Plan discussed:
Discounts offered:
Contract length discussed:
Constraints or approvals needed:

---
NEXT STEPS
Zach:
- action item

Customer:
- action item

Scheduled:
- Next meeting date:
- Materials to send:

---
ADDITIONAL CONTEXT
Personality, internal politics, seriousness level, gut feel

---
TRANSCRIPT: {fireflies_url}

For short calls (<10 min), use compact version:
Summary, Use Case, Qualification, Risks, Next Steps only.
Skip empty sections — don't write "Not discussed."
""")
    print(f"{'='*70}")
    print("MARKETING INTELLIGENCE (per call)")
    print(f"{'─'*70}")
    print("""In addition to features and notes, extract MARKETING INTELLIGENCE from
each call. This is for the marketing team — a different lens on the same
transcript data. Only include information EXPLICITLY stated in the call.
Do NOT fabricate or infer anything.

IMPORTANT: Skip marketing_data for INTERNAL calls (titles containing "sales",
"win & loss", or similar internal meeting names). Set those to null. Marketing
data is only for EXTERNAL customer/prospect calls.

Add a "marketing_data" object per call in the top-level JSON output:

  "marketing_data": {
    "<call_id>": {
      "company": "Company Name",
      "company_domain": "example.com (the company's website domain — infer from context, email domains, or explicit mention. Omit www. prefix)",
      "domain_confidence": "high | low | unresolved (high = explicitly stated or clearly from email domain; low = inferred from context; unresolved = could not determine)",
      "company_description": "Brief description based on what was said in the call",
      "industry": "Only if mentioned",
      "contacts": [
        {
          "name": "First Last",
          "title": "Job Title (only if stated)",
          "role_in_decision": "champion / decision-maker / evaluator (only if clear)"
        }
      ],
      "currently_evaluating": ["Feature/capability they are actively evaluating"],

      "quotes": [
        {
          "text": "Exact verbatim quote from the prospect",
          "speaker": "Speaker Name",
          "timestamp": "~MM:SS",
          "ts_seconds": 123,
          "theme": "problem_description | workaround | emotional | general"
        }
      ],

      "terminology": [
        {"prospect_term": "centers", "standard_term": "locations"}
      ],

      "questions_asked": [
        {
          "question": "Exact question the prospect asked",
          "speaker": "Speaker Name",
          "timestamp": "~MM:SS",
          "ts_seconds": 123
        }
      ],

      "objections": [
        {
          "objection": "Short description",
          "quote": "Verbatim quote if available",
          "speaker": "Speaker Name",
          "timestamp": "~MM:SS",
          "ts_seconds": 123
        }
      ],

      "competitors_mentioned": [
        {
          "name": "Competitor Name",
          "context": "current platform / considered alternative / etc.",
          "timestamp": "~MM:SS",
          "ts_seconds": 123
        }
      ],

      "barriers_to_adoption": ["Migration from existing platform", "Need IT approval"],

      "buying_signals": [
        {
          "signal": "Requested custom demo for regional directors",
          "interpretation": "champion building internal buy-in",
          "timestamp": "~MM:SS",
          "ts_seconds": 123
        }
      ],

      "timeline": "Q3 rollout mentioned" or null
    }
  }

DOMAIN RESOLUTION GUIDANCE for company_domain:
- Look for email addresses in attendee lists (e.g. jane@acmecorp.com → acmecorp.com)
- Look for explicit website mentions in the transcript
- If the company name is well-known, you may infer the domain (e.g. "Nike" → nike.com) — mark as "high"
- If you can only guess from context, mark as "low"
- If you truly cannot determine the domain, set company_domain to null and domain_confidence to "unresolved"

For each call, extract:
1. CONTACTS: Name, title (only if stated), role in buying decision (only if clear)
2. COMPANY CONTEXT: Description and industry based only on what was said
3. VERBATIM QUOTES: Most notable prospect quotes. Focus on problem descriptions,
   current workarounds, emotional language. Include exact timestamps.
4. TERMINOLOGY: Words/phrases the prospect uses that differ from Teachable's
   internal language. This is gold for marketing copy.
5. QUESTIONS ASKED: Direct questions prospects raised. Include timestamps.
6. OBJECTIONS: Hesitations, concerns, pushback. Include quotes + timestamps.
7. COMPETITORS: Products explicitly named. Include context + timestamps.
8. BUYING SIGNALS: Actions/statements indicating purchase intent. Include timestamps.
9. TIMELINE: Any mentions of timing, deadlines, urgency.
10. BARRIERS: Anything that could slow or prevent a deal.

If a field has no data, use an empty array [] or null. Do NOT fabricate.
""")


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
        marketing_report = raw.get("marketing_report", {})
        marketing_data_by_call = raw.get("marketing_data", {})
        segment_data_by_call = raw.get("segment_data", {})
        competitor_mentions_by_call = raw.get("competitor_mentions", {})
        junk_ids = set(raw.get("junk_ids", []))
    else:
        features_by_call = raw
        notes_by_call = {}
        recap_text = ""
        company_summaries = {}
        marketing_report = {}
        marketing_data_by_call = {}
        segment_data_by_call = {}
        competitor_mentions_by_call = {}
        junk_ids = set()

    # Load categories list for validation (optional fallback)
    valid_categories = set()
    categories_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
    if os.path.exists(categories_path):
        with open(categories_path, "r") as f:
            cats = json.load(f)
        valid_categories = {c["name"] for c in cats.get("categories", [])}

    # Load valid segments for validation
    valid_segments = set()
    segments_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "segments.json")
    if os.path.exists(segments_path):
        with open(segments_path, "r") as f:
            segs = json.load(f)
        valid_segments = {s["name"] for s in segs.get("segments", [])}

    # Load valid competitors for validation
    valid_competitors = set()
    competitors_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "competitors.json")
    if os.path.exists(competitors_path):
        with open(competitors_path, "r") as f:
            comps = json.load(f)
        valid_competitors = {c["name"] for c in comps.get("competitors", [])}
    valid_mention_types = VALID_MENTION_TYPES

    # Legacy: load categories map from --categories flag (optional)
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
    category_errors = []

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
            feat_type = feat.get("type", "prospect_request")

            # Build deep link
            deep_link = ""
            transcript_url = call.get("transcript", "")
            if ts_seconds is not None and transcript_url:
                base = transcript_url.split("?")[0]
                deep_link = f"{base}?t={int(ts_seconds)}"

            keyword_counts[feature_name] = keyword_counts.get(feature_name, 0) + 1

            # Category: prefer inline from analysis, fallback to map
            category = feat.get("category") or categories_map.get(feature_name, "Other")
            if valid_categories and category not in valid_categories and category != "NEEDS_REVIEW":
                suggestion = _suggest_match(category, valid_categories)
                hint = f' (did you mean "{suggestion}"?)' if suggestion else ""
                category_errors.append(f"  ERROR: \"{feature_name}\" has invalid category \"{category}\"{hint} (call {call_id[:12]})")
                category = "Other"

            mention_id = generate_mention_id(call_id, feature_name)

            new_mentions.append({
                "mention_id": mention_id,
                "call_id": call_id,
                "call_title": call.get("title", ""),
                "call_date": call.get("date", ""),
                "speaker": speaker,
                "company": feat.get("company", ""),
                "contact_title": feat.get("contact_title", ""),
                "keyword": feature_name,
                "category": category,
                "confidence": feat.get("confidence"),
                "type": feat_type,
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

    if category_errors:
        print("\n  CATEGORY VALIDATION FAILED — the following features used non-canonical categories:")
        for err in category_errors:
            print(err)
        print(f"\n  Valid categories: {sorted(valid_categories)}")
        print("  Tip: run `python3 analyze_features.py validate <file> --fix` to auto-correct.")
        sys.exit(1)

    # Clear pending_analysis flag on all calls that now have features
    for call in calls:
        call_id = call.get("id", "")
        if call_id in calls_with_features:
            call.pop("pending_analysis", None)
        elif call_id in junk_ids:
            call.pop("pending_analysis", None)
            call["is_junk"] = True

    # Merge: keep existing mentions for calls NOT in the new input, replace for calls that are
    existing_mentions = data.get("mentions", [])
    preserved = [m for m in existing_mentions if m.get("call_id") not in features_by_call]
    all_mentions = preserved + new_mentions

    # Rebuild stats from the complete merged dataset
    all_call_ids = {m.get("call_id") for m in all_mentions}
    all_keywords = {}
    for m in all_mentions:
        kw = m.get("keyword", "Unknown")
        all_keywords[kw] = all_keywords.get(kw, 0) + 1

    data["mentions"] = all_mentions
    data["stats"] = {
        "total_mentions": len(all_mentions),
        "unique_calls": len(all_call_ids),
        "unique_features": len(all_keywords),
        "generated": date.today().isoformat(),
    }
    if recap_text:
        data["recap"] = recap_text
    if company_summaries:
        data["company_summaries"] = company_summaries
    if marketing_report:
        data["marketing_report"] = marketing_report
    # Embed competitors catalog for dashboard UI
    if os.path.exists(competitors_path):
        with open(competitors_path, "r") as f:
            comp_catalog = json.load(f)
        data["competitors"] = comp_catalog.get("competitors", [])
        data["competitor_types"] = comp_catalog.get("competitor_types", {})
    # Store per-call marketing data on each call object
    if marketing_data_by_call:
        for call in calls:
            call_id = call.get("id", "")
            if call_id in marketing_data_by_call:
                mdata = marketing_data_by_call[call_id]
                call["marketing_data"] = mdata
                # Promote domain fields to top-level for easy access in aggregation
                if isinstance(mdata, dict):
                    if mdata.get("company_domain"):
                        call["company_domain"] = mdata["company_domain"]
                    if mdata.get("domain_confidence"):
                        call["domain_confidence"] = mdata["domain_confidence"]

    # Store per-call segment data on each call object
    segment_errors = []
    if segment_data_by_call:
        for call in calls:
            call_id = call.get("id", "")
            if call_id in segment_data_by_call:
                seg = segment_data_by_call[call_id]
                if seg:
                    segment_name = seg.get("segment")
                    if valid_segments and segment_name and segment_name != "NEEDS_REVIEW" and segment_name not in valid_segments:
                        suggestion = _suggest_match(segment_name, valid_segments)
                        hint = f' (did you mean "{suggestion}"?)' if suggestion else ""
                        segment_errors.append(f"  ERROR: Call {call_id[:12]} has invalid segment \"{segment_name}\"{hint}")
                        continue
                    call["segment"] = segment_name
                    call["segment_confidence"] = seg.get("segment_confidence")
                    call["segment_reasoning"] = seg.get("segment_reasoning")
                    call["alternative_segment"] = seg.get("alternative_segment")
                    call["suggested_new_segment"] = seg.get("suggested_new_segment")

    if segment_errors:
        print("\n  SEGMENT VALIDATION FAILED — the following calls used non-canonical segment names:")
        for err in segment_errors:
            print(err)
        print(f"\n  Valid segments: {sorted(valid_segments)}")
        print("  Tip: run `python3 analyze_features.py validate <file> --fix` to auto-correct.")
        sys.exit(1)

    # Store per-call competitor mentions on each call object
    competitor_errors = []
    if competitor_mentions_by_call:
        for call in calls:
            call_id = call.get("id", "")
            if call_id in competitor_mentions_by_call:
                mentions_list = competitor_mentions_by_call[call_id]
                if not isinstance(mentions_list, list):
                    continue
                validated = []
                for cm in mentions_list:
                    comp_name = cm.get("competitor", "")
                    # Resolve aliases (e.g. "School" -> "Skool")
                    resolved = _resolve_competitor_name(comp_name)
                    if resolved != comp_name:
                        cm["competitor"] = resolved
                        comp_name = resolved
                    if valid_competitors and comp_name and comp_name != "NEEDS_REVIEW" and comp_name not in valid_competitors:
                        suggestion = _suggest_match(comp_name, valid_competitors)
                        hint = f' (did you mean "{suggestion}"?)' if suggestion else ""
                        competitor_errors.append(f'  ERROR: Call {call_id[:12]} mentions invalid competitor "{comp_name}"{hint}')
                        continue
                    mt = cm.get("mention_type", "")
                    if mt and mt not in valid_mention_types:
                        competitor_errors.append(f'  ERROR: Call {call_id[:12]} has invalid mention_type "{mt}" for "{comp_name}"')
                        continue
                    validated.append(cm)
                if validated:
                    # Replace (not append) to prevent duplication on re-inject
                    call["competitor_mentions"] = validated

    if competitor_errors:
        print("\n  COMPETITOR VALIDATION FAILED:")
        for err in competitor_errors:
            print(err)
        print(f"\n  Valid competitors: {sorted(valid_competitors)}")
        print(f"  Valid mention types: {sorted(valid_mention_types)}")
        print("  Tip: run `python3 analyze_features.py validate <file> --fix` to auto-correct.")
        sys.exit(1)

    # Embed capability map for dashboard overlay
    cap_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'capability_map.json')
    if os.path.exists(cap_map_path):
        with open(cap_map_path) as f:
            cap_data = json.load(f)
        data['capability_map'] = cap_data.get('mapping', {})
        print(f"  Loaded capability map: {len(data['capability_map'])} features mapped")
    else:
        print("  WARNING: config/capability_map.json not found — overlay will be disabled")
        data['capability_map'] = {}

    # Write updated dashboard
    _write_data_to_html(args.dashboard, data)

    # Write canonical features.json alongside the dashboard
    output_dir = os.path.dirname(args.dashboard)
    write_canonical_json(data, output_dir)

    pending_remaining = sum(1 for c in calls if c.get("pending_analysis"))
    print(f"Updated dashboard: {len(new_mentions)} features across {len(calls_with_features)} calls")
    if pending_remaining:
        print(f"  {pending_remaining} call(s) still pending analysis")

    # Print category distribution
    cat_counts = {}
    for m in new_mentions:
        c = m.get("category", "Other")
        cat_counts[c] = cat_counts.get(c, 0) + 1
    print(f"\n  Category distribution:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")
    other_count = cat_counts.get("Other", 0)
    if other_count:
        print(f"\n  WARNING: {other_count} feature(s) categorized as 'Other'")

    # NEEDS_REVIEW summary
    nr_cats = [m for m in all_mentions if m.get("category") == "NEEDS_REVIEW"]
    nr_segs = [c for c in calls if c.get("segment") == "NEEDS_REVIEW"]
    nr_comps = [cm for c in calls for cm in c.get("competitor_mentions", []) if cm.get("competitor") == "NEEDS_REVIEW"]
    if nr_cats or nr_segs or nr_comps:
        print(f"\n  NEEDS REVIEW: {len(nr_cats)} feature(s), {len(nr_segs)} segment(s), {len(nr_comps)} competitor(s)")
        if nr_cats:
            for m in nr_cats:
                print(f"    - Feature \"{m.get('keyword')}\" (call {m.get('call_id', '')[:12]})")
        if nr_segs:
            for c in nr_segs:
                print(f"    - Segment for \"{c.get('title', '')}\" (call {c.get('id', '')[:12]})")
        if nr_comps:
            for cm in nr_comps:
                print(f"    - Competitor \"{cm.get('context', '')[:60]}\"")

    # Print competitor summary
    all_comp_mentions = [cm for c in calls for cm in c.get("competitor_mentions", [])]
    if all_comp_mentions:
        comp_counts = {}
        for cm in all_comp_mentions:
            n = cm.get("competitor", "?")
            comp_counts[n] = comp_counts.get(n, 0) + 1
        print(f"\n  Competitor mentions: {len(all_comp_mentions)} total")
        for comp, count in sorted(comp_counts.items(), key=lambda x: -x[1]):
            print(f"    {comp}: {count}")

    # Track suggested new categories/segments from NEEDS_REVIEW items
    today = date.today().isoformat()
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Collect suggested categories from raw features
    suggested_cats = {}
    for call_id, feats in features_by_call.items():
        for feat in feats:
            sc = feat.get("suggested_category")
            if sc and feat.get("category") == "NEEDS_REVIEW":
                if sc not in suggested_cats:
                    suggested_cats[sc] = {"name": sc, "first_seen": today, "count": 0, "examples": []}
                suggested_cats[sc]["count"] += 1
                fn = feat.get("feature", "")
                if fn and fn not in suggested_cats[sc]["examples"]:
                    suggested_cats[sc]["examples"].append(fn)

    if suggested_cats:
        cat_path = os.path.join(base_dir, "categories.json")
        if os.path.exists(cat_path):
            with open(cat_path, "r") as f:
                cat_data = json.load(f)
            existing = {s["name"]: s for s in cat_data.get("suggested_new_categories", [])}
            for name, info in suggested_cats.items():
                if name in existing:
                    existing[name]["count"] += info["count"]
                    for ex in info["examples"]:
                        if ex not in existing[name]["examples"]:
                            existing[name]["examples"].append(ex)
                else:
                    existing[name] = info
            cat_data["suggested_new_categories"] = list(existing.values())
            with open(cat_path, "w") as f:
                json.dump(cat_data, f, indent=2)
            print(f"\n  Updated categories.json with {len(suggested_cats)} suggested new categor{'y' if len(suggested_cats) == 1 else 'ies'}")

    # Collect suggested new segments
    suggested_segs = {}
    for call_id, seg in segment_data_by_call.items():
        if seg and seg.get("segment") == "NEEDS_REVIEW":
            sn = seg.get("suggested_new_segment")
            if sn:
                if sn not in suggested_segs:
                    suggested_segs[sn] = {"name": sn, "first_seen": today, "count": 0}
                suggested_segs[sn]["count"] += 1

    if suggested_segs:
        seg_path = os.path.join(base_dir, "segments.json")
        if os.path.exists(seg_path):
            with open(seg_path, "r") as f:
                seg_data = json.load(f)
            existing = {s["name"]: s for s in seg_data.get("suggested_new_segments", [])}
            for name, info in suggested_segs.items():
                if name in existing:
                    existing[name]["count"] += info["count"]
                else:
                    existing[name] = info
            seg_data["suggested_new_segments"] = list(existing.values())
            with open(seg_path, "w") as f:
                json.dump(seg_data, f, indent=2)
            print(f"  Updated segments.json with {len(suggested_segs)} suggested new segment(s)")

    # Optionally regenerate HubSpot notes
    if args.notes:
        with open(args.notes, "w") as f:
            for i, call in enumerate(calls):
                if i > 0:
                    f.write("\n\n" + "=" * 70 + "\n\n")
                f.write(call.get("hubspot_note", ""))
        print(f"Updated HubSpot notes: {args.notes}")

    # Optionally sync to Google Sheets
    if args.sync_sheets:
        try:
            from sync_to_sheets import sync
            result = sync(output_dir=output_dir)
            print(f"Sheet sync: {result['rows_added']} added, {result['rows_updated']} updated")
        except Exception as e:
            print(f"Sheet sync failed: {e}")

    # Auto-generate Clay snapshot (pass DATA directly to avoid re-parsing HTML)
    if not args.skip_snapshot:
        try:
            from lib.clay import generate_snapshot
            print("Auto-generating Clay snapshot...")
            result = generate_snapshot(data=data)
            if "error" in result:
                print(f"  Warning: snapshot generation failed: {result['error']}")
            else:
                seed_count = len(result.get("seed_companies", []))
                print(f"  Snapshot generated: {seed_count} seeds, {len(result.get('segments', []))} segments")
        except Exception as e:
            print(f"  Warning: snapshot generation failed: {e}")


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


def cmd_refetch_empty(args):
    """Re-fetch transcripts from Fireflies for calls with empty transcript_text."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

    api_key = os.getenv('FIREFLIES_API_KEY')
    if not api_key:
        print("Error: FIREFLIES_API_KEY not set in .env")
        sys.exit(1)

    from client import FirefliesRetriever

    data = _extract_data_from_html(args.dashboard)
    calls = data.get("calls", [])

    # Find calls with empty transcripts
    empty_calls = [c for c in calls if not c.get("transcript_text", "").strip()]

    if not empty_calls:
        print("All calls have transcript text. Nothing to refetch.")
        return

    print(f"Found {len(empty_calls)} call(s) with empty transcripts:\n")
    for c in empty_calls:
        print(f"  - {c.get('title', '?')} ({c.get('date', '?')}) [{c.get('id', '?')}]")
    print()

    retriever = FirefliesRetriever(api_key)
    updated = 0

    for call_data in empty_calls:
        call_id = call_data.get("id", "")
        if not call_id:
            continue

        print(f"Refetching: {call_data.get('title', '?')}...")
        call = retriever.fetch_single_transcript(call_id, verbose=True)

        if call and call.full_transcript_text:
            call_data["transcript_text"] = call.full_transcript_text
            # Also fix duration if it was wrong
            if call.duration > 0:
                call_data["duration"] = round(call.duration / 60) if call.duration > 300 else round(call.duration)
            # Fix attendees if empty
            if not call_data.get("attendees") and call.attendee_names:
                call_data["attendees"] = ", ".join(call.attendee_names)
            updated += 1
            print(f"  -> Got {len(call.full_transcript_text)} chars")
        else:
            print(f"  -> Still empty (transcript may not be processed yet)")

    if updated:
        _write_data_to_html(args.dashboard, data)
        print(f"\nUpdated {updated} call(s) with fresh transcripts.")
    else:
        print("\nNo transcripts were updated.")


VALID_MENTION_TYPES = {"currently_using", "switching_from", "evaluated", "asked_about", "compared_to"}

# Competitor name aliases — maps informal/misspelled names to canonical form
_COMPETITOR_ALIASES = {
    "school": "Skool",
}


def _resolve_competitor_name(name):
    """Resolve competitor aliases to canonical name."""
    return _COMPETITOR_ALIASES.get(name.lower(), name)


def _load_valid_names():
    """Load canonical category, segment, and competitor names from JSON files."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    valid_categories = set()
    valid_segments = set()
    valid_competitors = set()

    categories_path = os.path.join(base_dir, "categories.json")
    if os.path.exists(categories_path):
        with open(categories_path, "r") as f:
            cats = json.load(f)
        valid_categories = {c["name"] for c in cats.get("categories", [])}

    segments_path = os.path.join(base_dir, "segments.json")
    if os.path.exists(segments_path):
        with open(segments_path, "r") as f:
            segs = json.load(f)
        valid_segments = {s["name"] for s in segs.get("segments", [])}

    competitors_path = os.path.join(base_dir, "competitors.json")
    if os.path.exists(competitors_path):
        with open(competitors_path, "r") as f:
            comps = json.load(f)
        valid_competitors = {c["name"] for c in comps.get("competitors", [])}

    return valid_categories, valid_segments, valid_competitors


def _suggest_match(invalid_name, valid_names, cutoff=0.6):
    """Suggest the closest canonical name using fuzzy matching."""
    matches = get_close_matches(invalid_name, sorted(valid_names), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def validate_analysis(data, valid_categories, valid_segments, valid_competitors=None):
    """Validate an analysis JSON dict against canonical lists.

    Returns list of error dicts with keys: type, call_id, value, feature (if category),
    suggestion (closest match or None).
    """
    errors = []

    # Check feature categories
    for call_id, features in data.get("features", {}).items():
        for feat in features:
            category = feat.get("category", "")
            if category and category != "NEEDS_REVIEW" and category not in valid_categories:
                errors.append({
                    "type": "invalid_category",
                    "call_id": call_id,
                    "feature": feat.get("feature", "?"),
                    "value": category,
                    "suggestion": _suggest_match(category, valid_categories),
                })

    # Check segments
    for call_id, seg in data.get("segment_data", {}).items():
        if not seg:
            continue
        segment = seg.get("segment", "")
        if segment and segment != "NEEDS_REVIEW" and segment not in valid_segments:
            errors.append({
                "type": "invalid_segment",
                "call_id": call_id,
                "feature": None,
                "value": segment,
                "suggestion": _suggest_match(segment, valid_segments),
            })

    # Check competitor mentions
    if valid_competitors:
        for call_id, mentions in data.get("competitor_mentions", {}).items():
            if not isinstance(mentions, list):
                continue
            for cm in mentions:
                comp = cm.get("competitor", "")
                comp = _resolve_competitor_name(comp)
                if comp and comp != "NEEDS_REVIEW" and comp not in valid_competitors:
                    errors.append({
                        "type": "invalid_competitor",
                        "call_id": call_id,
                        "feature": None,
                        "value": comp,
                        "suggestion": _suggest_match(comp, valid_competitors),
                    })
                mt = cm.get("mention_type", "")
                if mt and mt not in VALID_MENTION_TYPES:
                    errors.append({
                        "type": "invalid_mention_type",
                        "call_id": call_id,
                        "feature": comp or "?",
                        "value": mt,
                        "suggestion": _suggest_match(mt, VALID_MENTION_TYPES),
                    })

    return errors


def _check_capability_map_coverage(data):
    """Check capability map coverage of features in analysis data."""
    cap_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'capability_map.json')
    if not os.path.exists(cap_map_path):
        print("\n  INFO: No config/capability_map.json found — skipping overlay validation")
        return
    with open(cap_map_path) as f:
        cap_data = json.load(f)
    cap_map = cap_data.get('mapping', {})
    map_keys_lower = {k.lower().strip() for k in cap_map.keys()}

    # Collect unique feature names from analysis
    unmapped = []
    features_dict = data.get("features", {})
    all_names = set()
    for call_feats in features_dict.values():
        if isinstance(call_feats, list):
            for f in call_feats:
                fname = (f.get('feature') or f.get('name') or f.get('keyword', '')).strip()
                if fname and fname.upper() != 'NEEDS_REVIEW':
                    all_names.add(fname)
    for fname in all_names:
        if fname.lower().strip() not in map_keys_lower:
            unmapped.append(fname)
    if unmapped:
        print(f"\n  WARNING: {len(unmapped)} features not in capability_map.json:")
        for fname in sorted(set(unmapped)):
            print(f"    - {fname}")
        print("\n  Run: python3 analyze_features.py map-features to generate mappings")
    else:
        print(f"\n  \u2713 All {len(all_names)} features have capability map entries")
    low_conf = [k for k, v in cap_map.items() if v.get('confidence') == 'low']
    if low_conf:
        print(f"\n  INFO: {len(low_conf)} features have low confidence:")
        for fname in sorted(low_conf)[:10]:
            print(f"    - {fname}: {cap_map[fname].get('tier')} ({cap_map[fname].get('coverage_notes', '')})")
        if len(low_conf) > 10:
            print(f"    ... and {len(low_conf) - 10} more")


def cmd_map_features(args):
    """Show unmapped features and optionally inject a mapping file."""
    # Load existing capability map
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cap_map_path = os.path.join(base_dir, 'config', 'capability_map.json')
    existing_map = {}
    if os.path.exists(cap_map_path):
        with open(cap_map_path) as f:
            existing_map = json.load(f).get('mapping', {})

    # Load features from dashboard HTML
    with open(args.dashboard, 'r') as f:
        html = f.read()
    m = re.search(r'let DATA = ({.*?});\s*(?://|</script>)', html, re.DOTALL)
    if not m:
        print("ERROR: Could not find DATA in dashboard HTML")
        sys.exit(1)
    data = json.loads(m.group(1))

    all_names = set()
    for mention in data.get('mentions', []):
        kw = mention.get('keyword', '').strip()
        if kw and kw.upper() != 'NEEDS_REVIEW':
            all_names.add(kw)

    map_keys_lower = {k.lower().strip() for k in existing_map.keys()}
    unmapped = sorted([n for n in all_names if n.lower().strip() not in map_keys_lower])

    print(f"Total unique features: {len(all_names)}")
    print(f"Already mapped: {len(all_names) - len(unmapped)}")
    print(f"Unmapped: {len(unmapped)}")

    if hasattr(args, 'inject_file') and args.inject_file:
        # Merge new mappings into existing
        with open(args.inject_file) as f:
            new_data = json.load(f)
        new_mapping = new_data.get('mapping', new_data)
        merged = dict(existing_map)
        added = 0
        for k, v in new_mapping.items():
            if k not in merged:
                added += 1
            merged[k] = v
        output = {
            "version": 2,
            "source": "Teachable_Platform_Expert_Skill.md",
            "source_date": "2026-02",
            "last_generated": date.today().isoformat() + "T00:00:00Z",
            "feature_count": len(merged),
            "mapping": merged
        }
        os.makedirs(os.path.dirname(cap_map_path), exist_ok=True)
        with open(cap_map_path, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"\nMerged {added} new + {len(new_mapping) - added} updated entries into {cap_map_path}")
        print(f"Total mapped: {len(merged)}")
    elif unmapped:
        print(f"\nUnmapped feature names (paste into generation prompt):")
        for name in unmapped:
            print(f"  {name}")
    else:
        print("\nAll features are mapped!")

    # Show tier distribution
    if existing_map:
        tiers = {}
        for v in existing_map.values():
            t = v.get('tier', 'unknown')
            tiers[t] = tiers.get(t, 0) + 1
        print(f"\nCurrent tier distribution:")
        for t in ['native', 'workaround', 'roadmap', 'gap', 'unknown']:
            print(f"  {t}: {tiers.get(t, 0)}")


def cmd_validate(args):
    """Validate an analysis JSON against canonical category/segment/competitor lists."""
    with open(args.analysis_json, "r") as f:
        data = json.load(f)

    valid_categories, valid_segments, valid_competitors = _load_valid_names()

    if not valid_categories:
        print("WARNING: categories.json not found, skipping category validation")
    if not valid_segments:
        print("WARNING: segments.json not found, skipping segment validation")
    if not valid_competitors:
        print("WARNING: competitors.json not found, skipping competitor validation")

    errors = validate_analysis(data, valid_categories, valid_segments, valid_competitors)

    if not errors:
        # Count items
        feat_count = sum(len(v) for v in data.get("features", {}).values())
        seg_count = len(data.get("segment_data", {}))
        comp_count = sum(len(v) for v in data.get("competitor_mentions", {}).values() if isinstance(v, list))
        needs_review_cats = sum(
            1 for feats in data.get("features", {}).values()
            for f in feats if f.get("category") == "NEEDS_REVIEW"
        )
        needs_review_segs = sum(
            1 for s in data.get("segment_data", {}).values()
            if s and s.get("segment") == "NEEDS_REVIEW"
        )
        needs_review_comps = sum(
            1 for mentions in data.get("competitor_mentions", {}).values()
            if isinstance(mentions, list)
            for cm in mentions if cm.get("competitor") == "NEEDS_REVIEW"
        )
        print(f"VALID: {feat_count} features, {seg_count} segments, {comp_count} competitor mentions — all canonical.")
        if needs_review_cats or needs_review_segs or needs_review_comps:
            print(f"  NEEDS_REVIEW: {needs_review_cats} feature(s), {needs_review_segs} segment(s), {needs_review_comps} competitor(s)")
        _check_capability_map_coverage(data)
        sys.exit(0)

    # Print errors
    print(f"VALIDATION FAILED: {len(errors)} error(s)\n")
    for err in errors:
        if err["type"] == "invalid_category":
            print(f'  [{err["call_id"][:12]}] Feature "{err["feature"]}" has invalid category "{err["value"]}"')
        elif err["type"] == "invalid_segment":
            print(f'  [{err["call_id"][:12]}] Invalid segment "{err["value"]}"')
        elif err["type"] == "invalid_competitor":
            print(f'  [{err["call_id"][:12]}] Invalid competitor "{err["value"]}"')
        elif err["type"] == "invalid_mention_type":
            print(f'  [{err["call_id"][:12]}] Invalid mention_type "{err["value"]}" for competitor "{err["feature"]}"')
        if err.get("suggestion"):
            print(f'    Did you mean: "{err["suggestion"]}"?')
        else:
            print(f'    No close match found. Use "NEEDS_REVIEW" if it doesn\'t fit.')

    if args.fix:
        _apply_fixes(data, errors, args.analysis_json)
    else:
        print(f"\n  Run with --fix to auto-correct obvious mismatches.")
        sys.exit(1)

    # Check capability map coverage
    _check_capability_map_coverage(data)


def _apply_fixes(data, errors, output_path):
    """Auto-correct analysis JSON based on validation errors.

    Uses a 0.6 cutoff for auto-fix — with only 10 categories, 9 segments,
    and 16 competitors, any match above 0.6 is unambiguously the right name.
    """
    FIX_CUTOFF = 0.6
    auto_fixed = 0
    needs_review = 0
    valid_categories, valid_segments, valid_competitors = _load_valid_names()

    for err in errors:
        if err["type"] == "invalid_category":
            for feat in data.get("features", {}).get(err["call_id"], []):
                if feat.get("category") == err["value"] and feat.get("feature") == err["feature"]:
                    suggestion = _suggest_match(err["value"], valid_categories, cutoff=FIX_CUTOFF)
                    if suggestion:
                        feat["category"] = suggestion
                        auto_fixed += 1
                        print(f'  AUTO-FIX: "{err["value"]}" -> "{suggestion}" ({err["feature"]})')
                    else:
                        feat["suggested_category"] = feat.get("suggested_category") or err["value"]
                        feat["category"] = "NEEDS_REVIEW"
                        needs_review += 1
                        print(f'  NEEDS_REVIEW: "{err["value"]}" ({err["feature"]})')
                    break
        elif err["type"] == "invalid_segment":
            seg = data.get("segment_data", {}).get(err["call_id"])
            if seg and seg.get("segment") == err["value"]:
                suggestion = _suggest_match(err["value"], valid_segments, cutoff=FIX_CUTOFF)
                if suggestion:
                    seg["segment"] = suggestion
                    auto_fixed += 1
                    print(f'  AUTO-FIX: "{err["value"]}" -> "{suggestion}" (segment)')
                else:
                    seg["suggested_new_segment"] = seg.get("suggested_new_segment") or err["value"]
                    seg["segment"] = "NEEDS_REVIEW"
                    needs_review += 1
                    print(f'  NEEDS_REVIEW: "{err["value"]}" (segment)')
        elif err["type"] == "invalid_competitor":
            mentions = data.get("competitor_mentions", {}).get(err["call_id"], [])
            for cm in mentions:
                if cm.get("competitor") == err["value"]:
                    suggestion = _suggest_match(err["value"], valid_competitors, cutoff=FIX_CUTOFF)
                    if suggestion:
                        cm["competitor"] = suggestion
                        auto_fixed += 1
                        print(f'  AUTO-FIX: "{err["value"]}" -> "{suggestion}" (competitor)')
                    else:
                        cm["competitor"] = "NEEDS_REVIEW"
                        needs_review += 1
                        print(f'  NEEDS_REVIEW: "{err["value"]}" (competitor)')
                    break
        elif err["type"] == "invalid_mention_type":
            mentions = data.get("competitor_mentions", {}).get(err["call_id"], [])
            for cm in mentions:
                if cm.get("mention_type") == err["value"]:
                    suggestion = _suggest_match(err["value"], VALID_MENTION_TYPES, cutoff=FIX_CUTOFF)
                    if suggestion:
                        cm["mention_type"] = suggestion
                        auto_fixed += 1
                        print(f'  AUTO-FIX: mention_type "{err["value"]}" -> "{suggestion}"')
                    else:
                        cm["mention_type"] = "asked_about"
                        needs_review += 1
                        print(f'  FALLBACK: mention_type "{err["value"]}" -> "asked_about"')
                    break

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nFixed: {auto_fixed} auto-corrected, {needs_review} set to NEEDS_REVIEW")
    print(f"Saved: {output_path}")

    # Re-validate to confirm
    valid_categories, valid_segments, valid_competitors = _load_valid_names()
    remaining = validate_analysis(data, valid_categories, valid_segments, valid_competitors)
    if remaining:
        print(f"\nWARNING: {len(remaining)} error(s) remain after fix")
        sys.exit(1)
    else:
        print("All values now valid.")
        sys.exit(0)


def cmd_cleanup(args):
    """Fix company fields and remove invalid internal-speaker mentions."""
    data = _extract_data_from_html(args.dashboard)
    calls = data.get("calls", [])
    mentions = data.get("mentions", [])

    # Build call_id → company mapping
    call_companies = {}
    for call in calls:
        call_companies[call.get("id", "")] = _infer_company_from_call(call)

    cleaned = []
    removed = 0
    fixed = 0

    for m in mentions:
        speaker = m.get("speaker", "")
        feat_type = m.get("type", "")

        # Drop ALL mentions attributed to internal Teachable speakers.
        # The sales rep is on every call but is never the source of a feature.
        if _is_internal_speaker(speaker):
            removed += 1
            continue

        # Fill in empty company
        if not m.get("company") or m["company"].lower() in ("teachable", "unknown"):
            inferred = call_companies.get(m.get("call_id", ""), "")
            if inferred:
                m["company"] = inferred
                fixed += 1

        cleaned.append(m)

    data["mentions"] = cleaned

    # Rebuild stats
    all_call_ids = {m.get("call_id") for m in cleaned}
    all_keywords = {}
    for m in cleaned:
        kw = m.get("keyword", "")
        all_keywords[kw] = all_keywords.get(kw, 0) + 1

    data["stats"] = {
        "total_mentions": len(cleaned),
        "unique_calls": len(all_call_ids),
        "unique_features": len(all_keywords),
        "generated": data.get("stats", {}).get("generated", ""),
    }

    # Write updated dashboard + features.json
    _write_data_to_html(args.dashboard, data)
    output_dir = os.path.dirname(args.dashboard)
    write_canonical_json(data, output_dir)

    print(f"Cleanup complete:")
    print(f"  {fixed} company fields filled in")
    print(f"  {removed} internal-speaker mentions removed")
    print(f"  {len(cleaned)} mentions remaining")

    # Show company distribution
    companies = {}
    for m in cleaned:
        c = m.get("company", "(empty)")
        companies[c] = companies.get(c, 0) + 1
    print(f"\n  Company distribution:")
    for c, count in sorted(companies.items(), key=lambda x: -x[1]):
        print(f"    {c}: {count}")


def main():
    parser = argparse.ArgumentParser(description="AI feature analysis helper")
    sub = parser.add_subparsers(dest="command")

    # Extract
    p_extract = sub.add_parser("extract", help="Print call transcripts from dashboard")
    p_extract.add_argument("dashboard", help="Path to dashboard HTML file")
    p_extract.add_argument("--titles-only", action="store_true",
                           help="Only print call titles, not full transcripts")
    p_extract.add_argument("--all", action="store_true",
                           help="Extract all calls, not just pending/unanalyzed")
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
    p_inject.add_argument("--sync-sheets", action="store_true",
                          help="Sync to Google Sheet after injecting features")
    p_inject.add_argument("--skip-snapshot", action="store_true",
                          help="Skip auto-generating Clay snapshot after inject")

    # Validate
    p_validate = sub.add_parser("validate",
                                help="Validate analysis JSON against canonical categories/segments")
    p_validate.add_argument("analysis_json", help="Path to analysis JSON file")
    p_validate.add_argument("--fix", action="store_true",
                            help="Auto-correct obvious mismatches (>0.85 fuzzy), set rest to NEEDS_REVIEW")

    # Refetch empty transcripts
    p_refetch = sub.add_parser("refetch-empty",
                               help="Re-pull transcripts from Fireflies for calls with empty transcript_text")
    p_refetch.add_argument("dashboard", help="Path to dashboard HTML file")

    # Cleanup
    p_cleanup = sub.add_parser("cleanup",
                               help="Fix company fields and remove invalid internal-speaker mentions")
    p_cleanup.add_argument("dashboard", help="Path to dashboard HTML file")

    # Map features — capability overlay helper
    p_mapf = sub.add_parser("map-features",
                             help="Show unmapped features and manage capability_map.json")
    p_mapf.add_argument("dashboard", help="Path to dashboard HTML file")
    p_mapf.add_argument("--inject", dest="inject_file", metavar="FILE",
                         help="JSON file with new mappings to merge into capability_map.json")

    args = parser.parse_args()
    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "normalize":
        cmd_normalize(args)
    elif args.command == "inject":
        cmd_inject(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "refetch-empty":
        cmd_refetch_empty(args)
    elif args.command == "cleanup":
        cmd_cleanup(args)
    elif args.command == "map-features":
        cmd_map_features(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
