#!/usr/bin/env python3
"""
Fetch closed-won and closed-lost deals from HubSpot, extract feature and
competitor signals from deal notes via Haiku, and write win_loss.json.

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
            "notes_on_customer_feedback",
            "product_feedback",       # multi-select
            "loss_type",
            "kb_or_pd_deal",
            "uses_competitor_platform",
            "competitor_platform",
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

EXTRACTION_PROMPT = """You are analyzing closed sales deal notes to extract product and competitive signals.

Deal outcome: {outcome}
Deal amount: {amount}
Notes:
{notes}

Return JSON only. No preamble, no markdown:
{{
  "features_mentioned": [
    {{
      "feature": "canonical feature name",
      "sentiment": "positive|negative|neutral",
      "quote": "excerpt under 25 words"
    }}
  ],
  "competitors_mentioned": ["exact competitor name"],
  "pricing_signals": [
    {{
      "sentiment": "positive|negative|neutral",
      "reason": "Price too high | Discounting required | Packaging concern | ROI question | Budget constraint | Pricing was fine",
      "quote": "excerpt under 25 words"
    }}
  ],
  "loss_outcome_type": "competitive_loss | no_decision | price_budget | product_gap | timing | bad_fit | unknown",
  "win_reason": "one sentence why we won (WON deals only, else null)",
  "loss_reason": "one sentence why we lost (LOST deals only, else null)"
}}

Rules:
- features_mentioned: only features the PROSPECT asked about, reacted to, or cited as decisive
  WON deals: features key to the decision | LOST deals: features that were missing, inadequate, or caused hesitation
- competitors_mentioned: only if explicitly named by the prospect. Never infer
- pricing_signals: any mention of cost, budget, pricing concern, discounting, or packaging
  Do NOT put pricing concerns in features_mentioned
- loss_outcome_type: classify the primary reason the deal was lost:
    competitive_loss = prospect chose a named competitor
    no_decision     = prospect went dark, stalled, or chose to do nothing
    price_budget    = primary objection was cost/budget, no specific competitor named
    product_gap     = missing feature was the stated reason, no strong competitor
    timing          = "not now," "maybe next quarter," project deprioritized
    bad_fit         = wrong use case, wrong size, wrong segment
    unknown         = notes don't give enough signal to classify
  (For WON deals, return null)
- Use canonical Teachable feature names (e.g. "Custom Domain" not "white label domain")
- If notes are too thin for a field, return an empty array or null. Do not guess
- Return valid JSON only"""


def extract_deal_signals(deal_id: str, outcome: str, amount: str,
                         note_objects: list, cache: dict,
                         client, deal_property_text: str = "") -> dict:
    """Extract signals from deal notes. Uses cache keyed on note hash.

    note_objects: list of dicts with {note_id, createdate, body} or legacy list[str].
    deal_property_text: structured deal properties prepended before notes.
    """
    note_hash = _note_hash(note_objects)
    cache_key = f"{deal_id}:{note_hash}"

    if cache_key in cache:
        logger.debug("Cache hit: %s", deal_id)
        return cache[cache_key]

    bodies = [n["body"] if isinstance(n, dict) else n for n in note_objects]
    notes_text = "\n\n---\n\n".join(bodies)
    # Prepend structured deal properties as primary signal source
    if deal_property_text:
        notes_text = deal_property_text + "\n\n" + notes_text
    prompt = EXTRACTION_PROMPT.format(
        outcome=outcome,
        amount=f"${float(amount):,.0f}" if amount else "unknown",
        notes=notes_text,
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        raw_text = response.content[0].text
        # Strip markdown code fences if present
        if raw_text.strip().startswith("```"):
            raw_text = re.sub(r'^```\w*\n?', '', raw_text.strip())
            raw_text = re.sub(r'\n?```$', '', raw_text.strip())
        result = json.loads(raw_text)
    except (json.JSONDecodeError, IndexError) as e:
        logger.warning("Extraction parse error for deal %s: %s", deal_id, e)
        result = {
            "features_mentioned": [], "competitors_mentioned": [],
            "pricing_signals": [], "loss_outcome_type": None,
            "win_reason": None, "loss_reason": None,
        }

    result["_note_hash"] = note_hash
    result["_extracted_at"] = datetime.now(UTC).isoformat()
    result["_model"] = "claude-haiku-4-5-20251001"
    cache[cache_key] = result
    return result


def _has_signal(signals: dict) -> bool:
    """Return True if Haiku returned any useful signal."""
    return bool(
        signals.get("features_mentioned") or
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
    """Validate feature and competitor names. Unknown names get NEEDS_REVIEW."""
    for feat in signals.get("features_mentioned", []):
        name = feat.get("feature", "")
        if name and name not in canonical_features:
            feat["original_feature"] = name
            feat["feature"] = "NEEDS_REVIEW"
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
                        help="Use Anthropic API for extraction (requires ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    # Fail-closed: require exactly one extraction mode before writing production output
    modes = sum([args.dry_run, args.dump_notes is not None, args.signals_file is not None, args.api_extract])
    if modes == 0:
        # Auto-detect: if ANTHROPIC_API_KEY is set, use API extraction
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            args.api_extract = True
            logger.info("Auto-detected ANTHROPIC_API_KEY, enabling --api-extract")
        else:
            raise SystemExit(
                "ERROR: No extraction mode specified. Use one of:\n"
                "  --api-extract      (requires ANTHROPIC_API_KEY)\n"
                "  --signals-file X   (pre-extracted signals JSON)\n"
                "  --dump-notes X     (export notes for manual extraction)\n"
                "  --dry-run          (coverage preview, no signals)"
            )

    # Dry-run safety: don't overwrite production data with empty signals
    if args.dry_run:
        prod_path = "test_output/win_loss.json"
        if args.out == prod_path and not args.write_dry_run:
            args.out = prod_path.replace(".json", ".dry_run.json")
            logger.info("Dry-run: redirecting output to %s (use --write-dry-run to override)", args.out)

    token = os.getenv("HUBSPOT_TOKEN", "")
    if not token:
        raise SystemExit("ERROR: HUBSPOT_TOKEN not set")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if args.api_extract and not anthropic_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY not set (required for --api-extract)")

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

    classified_deals = []
    closed_won_fetched = 0
    closed_lost_fetched = 0
    excluded_by_owner = 0
    for raw_deal in raw_deals:
        props = raw_deal.get("properties", {})
        stage_id = props.get("dealstage", "")
        if stage_id in won_stage_set:
            outcome = "WON"
        elif stage_id in lost_stage_set:
            outcome = "LOST"
        else:
            continue

        # Check owner exclusion by email (deterministic, no name-parsing fragility)
        owner_id = props.get("hubspot_owner_id", "")
        owner_info = owners.get(owner_id, {})
        owner_email = (owner_info.get("email") or "").lower()
        owner_name = owner_info.get("name", "")
        if owner_email in EXCLUDE_OWNER_EMAILS:
            logger.debug("Excluding deal owned by %s (%s)", owner_name, owner_email)
            excluded_by_owner += 1
            continue

        if outcome == "WON":
            closed_won_fetched += 1
        else:
            closed_lost_fetched += 1
        classified_deals.append((raw_deal, outcome))

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
                    "notes_on_customer_feedback": props.get("notes_on_customer_feedback"),
                    "product_feedback": props.get("product_feedback"),
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

    # Load pre-extracted signals if provided
    signals_lookup = {}
    if args.signals_file:
        with open(args.signals_file) as f:
            signals_list = json.load(f)
        for s in signals_list:
            signals_lookup[s["deal_id"]] = s.get("signals", s)
        logger.info("Loaded %d pre-extracted signals from %s", len(signals_lookup), args.signals_file)

    # Step 5: Load cache + canonical data
    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        logger.info("Loaded %d cached extractions", len(cache))

    canonical_features, canonical_competitors = _load_canonical_sets()
    logger.info("Canonical: %d features, %d competitors", len(canonical_features), len(canonical_competitors))

    # Step 6: Initialize Anthropic client (only if --api-extract)
    anthropic_client = None
    if args.api_extract and not args.dry_run:
        import anthropic
        anthropic_client = anthropic.Anthropic(api_key=anthropic_key)

    # Step 7: Process each deal (only those with substantive notes)
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

        # Only process deals with substantive notes
        notes = deal_notes.get(did)
        if not notes:
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

        # Build structured property text (primary signal source when populated)
        deal_prop_text = _build_deal_property_text(props)

        # Extraction
        signals = {
            "features_mentioned": [], "competitors_mentioned": [],
            "pricing_signals": [], "loss_outcome_type": None,
            "win_reason": None, "loss_reason": None,
        }
        note_hash = _note_hash(notes)
        extracted_at = ""

        if did in signals_lookup:
            # Use pre-extracted signals (from --signals-file)
            signals = signals_lookup[did]
            signals = _validate_extracted_signals(signals, canonical_features, canonical_competitors)
            extracted_at = signals.get("_extracted_at", datetime.now(UTC).isoformat())
        elif anthropic_client:
            signals = extract_deal_signals(did, outcome, str(amount) if amount else "",
                                           notes, cache, anthropic_client,
                                           deal_property_text=deal_prop_text)
            signals = _validate_extracted_signals(signals, canonical_features, canonical_competitors)
            note_hash = signals.get("_note_hash", note_hash)
            extracted_at = signals.get("_extracted_at", "")

        # Post-extraction gate: skip deals with no signal from rankings
        has_extraction = bool(signals_lookup.get(did) or anthropic_client)
        if has_extraction and not _has_signal(signals):
            deals_no_signal += 1
            continue

        if outcome == "WON":
            analyzed_won += 1
            analyzed_value["won_known"] += amount if not is_estimated else 0
        else:
            analyzed_lost += 1
            if is_estimated:
                analyzed_value["lost_estimated"] += amount
            else:
                analyzed_value["lost_known"] += amount

        # Accumulate feature signals
        for feat in signals.get("features_mentioned", []):
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

        # Accumulate competitor signals
        for comp in signals.get("competitors_mentioned", []):
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
            "close_lost_reason_field": props.get("hs_closed_lost_reason"),
            "kb_or_pd_deal": props.get("kb_or_pd_deal"),
            "loss_type": props.get("loss_type"),
            "competitor_platform": props.get("competitor_platform"),
            "has_structured_properties": bool(deal_prop_text),
            "features_mentioned": signals.get("features_mentioned", []),
            "competitors_mentioned": signals.get("competitors_mentioned", []),
            "pricing_signals": signals.get("pricing_signals", []),
            "loss_outcome_type": signals.get("loss_outcome_type"),
            "win_reason": signals.get("win_reason"),
            "loss_reason": signals.get("loss_reason"),
            "notes": [{"note_id": n["note_id"], "createdate": n["createdate"]}
                      for n in notes] if isinstance(notes[0], dict) else [],
            "notes_count": len(notes),
            "note_hash": note_hash,
            "extracted_at": extracted_at,
        })

    # Build summary
    deals_with_substantive_notes = len(deal_notes)
    has_signal = bool(
        any(feature_signals[k] for k in feature_signals) or
        any(competitor_signals[k] for k in competitor_signals) or
        any(d.get("win_reason") or d.get("loss_reason") or d.get("pricing_signals")
            for d in deals_output)
    )
    extraction_status = "dry_run" if args.dry_run else ("extracted" if (anthropic_client or signals_lookup) else "not_run")

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
        "extraction_model": "claude-haiku-4-5-20251001",
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

    # Save cache
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    logger.info("Cache saved (%d entries)", len(cache))

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
