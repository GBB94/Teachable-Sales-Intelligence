"""
Data transforms for Clay.com v3 integration.

Handles company aggregation (kept from v2), ICP snapshot generation,
seed selection, exclude list generation, re-engagement detection,
credit estimation, and payload generation.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta

from .scoring import calculate_score, get_config
from .validator import validate_seed_payload, validate_exclude_payload, validate_re_engagement_payload

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_PATH = os.path.join(BASE_DIR, "..", "..", "data", "last_snapshot.json")

# ---------------------------------------------------------------------------
# Company name normalization (kept from v2)
# ---------------------------------------------------------------------------
_SUFFIX_RE = re.compile(
    r",?\s*\b(Inc\.?|LLC\.?|Ltd\.?|Corp\.?|Co\.?|PLC|GmbH|SA|SAS|AG)\s*$",
    re.IGNORECASE,
)

_TITLE_PATTERNS = [
    re.compile(r"(?:Demo|Discovery Call|Sales Call|Followup|Follow-up|Follow up|Meeting)\s*(?:with|[-\u2013])\s*(.+)", re.IGNORECASE),
    re.compile(r"(.+?)\s*(?:Demo|Discovery|Sales Call|Followup|Follow-up)", re.IGNORECASE),
]


def normalize_company_name(name):
    """Strip whitespace, remove Inc./LLC suffixes."""
    if not name:
        return ""
    name = name.strip()
    name = _SUFFIX_RE.sub("", name).strip()
    name = name.rstrip(" ,-.")
    return name


def slugify(name):
    """Generate a deterministic company_id from company name."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def _infer_company_from_call(call_title):
    """Extract company name from a call title."""
    if not call_title:
        return ""
    for pattern in _TITLE_PATTERNS:
        m = pattern.match(call_title.strip())
        if m:
            return m.group(1).strip()
    return call_title.strip()


# ---------------------------------------------------------------------------
# Domain normalization (new in v3)
# ---------------------------------------------------------------------------
def normalize_domain(raw):
    """Strip www., lowercase, strip whitespace. Return empty string if None/empty."""
    if not raw:
        return ""
    d = str(raw).strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


# ---------------------------------------------------------------------------
# Scoring config hash (new in v3)
# ---------------------------------------------------------------------------
def generate_scoring_config_hash(config):
    """
    Hash scoring-relevant portions of config (signals, boosts, max_score).
    Ignores snapshot_config, credit_controls, etc. so those changes don't
    suppress re-engagement alerts.
    """
    scoring_data = {
        "max_score": config.get("max_score"),
        "signals": config.get("signals", []),
        "segment_boosts": config.get("segment_boosts", {}),
        "feature_boosts": config.get("feature_boosts", {}),
    }
    serialized = json.dumps(scoring_data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Idempotency key generation (new in v3)
# ---------------------------------------------------------------------------
def generate_idempotency_key(key_type, **kwargs):
    """
    Generate idempotency keys for Clay payloads.

    key_type="seed": seed_set_id + normalized_domain (fallback: seed_set_id + company_id)
    key_type="exclude": normalized_domain (fallback: "linkedin:" + linkedin_url)
    key_type="re_engagement": company_id + ISO week (yyyy-Www)
    """
    if key_type == "seed":
        seed_set_id = kwargs.get("seed_set_id", "")
        domain = kwargs.get("normalized_domain", "")
        company_id = kwargs.get("company_id", "")
        if domain:
            return f"{seed_set_id}_{domain}"
        return f"{seed_set_id}_{company_id}"

    elif key_type == "exclude":
        domain = kwargs.get("normalized_domain", "")
        linkedin = kwargs.get("linkedin_url", "")
        if domain:
            return domain
        if linkedin:
            return f"linkedin:{linkedin}"
        return ""

    elif key_type == "re_engagement":
        company_id = kwargs.get("company_id", "")
        now = kwargs.get("now", datetime.now(timezone.utc))
        iso_cal = now.isocalendar()
        week_str = f"{iso_cal[0]}-W{iso_cal[1]:02d}"
        return f"{company_id}_{week_str}"

    return ""


# ---------------------------------------------------------------------------
# Company aggregation (kept from v2)
# ---------------------------------------------------------------------------
# Companies to never seed (your own company / internal domains)
_SELF_COMPANY_NAMES = {"teachable"}
_SELF_DOMAINS = {"teachable.com"}


def aggregate_companies(data):
    """
    Aggregate raw dashboard DATA (mentions + calls) into per-company prospects.
    Returns dict mapping company_slug -> prospect dict.

    Company resolution priority:
      1. mention["company"] (set by CC during analysis)
      2. call marketing_data["company"]
      3. _infer_company_from_call(call_title) (regex fallback)
    """
    calls_by_id = {}
    for call in data.get("calls", []):
        calls_by_id[call["id"]] = call

    company_data = {}

    for mention in data.get("mentions", []):
        call_id = mention.get("call_id", "")
        call_title = mention.get("call_title", "")
        call = calls_by_id.get(call_id, {})
        marketing = call.get("marketing_data", {}) if isinstance(call.get("marketing_data"), dict) else {}

        # Resolve company name: mention field > marketing_data > title inference
        raw_company = mention.get("company", "").strip()
        if not raw_company:
            raw_company = marketing.get("company", "").strip()
        if not raw_company:
            raw_company = _infer_company_from_call(call_title)

        company_name = normalize_company_name(raw_company)
        if not company_name:
            continue

        # Skip self-company
        if company_name.lower() in _SELF_COMPANY_NAMES:
            continue

        if company_name not in company_data:
            company_data[company_name] = {
                "company_name": company_name,
                "features_requested": [],
                "competitors_mentioned": [],
                "objections": [],
                "call_ids": set(),
                "call_dates": [],
                "segment": "",
                "domain": "",
                "domain_confidence": "unresolved",
                "attendees": set(),
            }

        cd = company_data[company_name]

        keyword = mention.get("keyword", "")
        if keyword and keyword not in cd["features_requested"]:
            cd["features_requested"].append(keyword)

        cd["call_ids"].add(call_id)
        call_date = mention.get("call_date") or call.get("date", "")
        if call_date and call_date not in cd["call_dates"]:
            cd["call_dates"].append(call_date)

        # Pull segment from call if not yet set
        if not cd["segment"]:
            cd["segment"] = call.get("segment", "")

        # Pull domain — prefer company_domain (from analysis) over marketing_data fallback
        # Confidence priority: high > low > unresolved
        _CONF_RANK = {"high": 3, "low": 2, "unresolved": 1}
        call_domain = call.get("company_domain", "")
        call_conf = call.get("domain_confidence", "unresolved")
        if call_domain and _CONF_RANK.get(call_conf, 0) > _CONF_RANK.get(cd["domain_confidence"], 0):
            cd["domain"] = call_domain
            cd["domain_confidence"] = call_conf
        elif not cd["domain"] and marketing:
            domain_raw = marketing.get("domain", "") or marketing.get("website", "")
            if domain_raw:
                cd["domain"] = domain_raw
                cd["domain_confidence"] = "low"

        speaker = mention.get("speaker", "")
        if speaker:
            cd["attendees"].add(speaker)

        for comp in call.get("competitor_mentions", []):
            if comp not in cd["competitors_mentioned"]:
                cd["competitors_mentioned"].append(comp)
        if marketing:
            for comp in marketing.get("competitors_mentioned", []):
                if comp not in cd["competitors_mentioned"]:
                    cd["competitors_mentioned"].append(comp)

    prospects = {}
    for company_name, cd in company_data.items():
        slug = slugify(company_name)
        dates = sorted(cd["call_dates"]) if cd["call_dates"] else []

        # Skip self-domains
        domain = normalize_domain(cd.get("domain", ""))
        if domain in _SELF_DOMAINS:
            continue

        prospects[slug] = {
            "prospect_id": slug,
            "company_id": slug,
            "company_name": company_name,
            "segment": cd["segment"],
            "domain": cd.get("domain", ""),
            "domain_confidence": cd.get("domain_confidence", "unresolved"),
            "features_requested": cd["features_requested"],
            "competitors_mentioned": cd["competitors_mentioned"],
            "objections": cd["objections"],
            "call_count": len(cd["call_ids"]),
            "total_feature_mentions": len(cd["features_requested"]),
            "first_call_date": dates[0] if dates else "",
            "last_call_date": dates[-1] if dates else "",
            "attendees": sorted(cd["attendees"]),
        }

    return prospects


# ---------------------------------------------------------------------------
# CSV helper
# ---------------------------------------------------------------------------
def _make_csv(items):
    """Semicolon-delimited CSV. Sanitizes semicolons in values first."""
    if not items:
        return ""
    cleaned = [str(item).replace(";", ",") for item in items]
    return "; ".join(cleaned)


# ---------------------------------------------------------------------------
# Seed selection algorithm (new in v3)
# ---------------------------------------------------------------------------
def select_seed_companies(scored_companies, config, last_snapshot=None):
    """
    Deterministic seed selection algorithm.

    1. Filter to companies meeting min_seed_score and min_calls_for_seed.
    2. Sort by score descending.
    3. Walk the list. Add each company if its segment hasn't hit max_seeds_per_segment.
    4. If a segment is full, skip and continue.
    5. Stop at max_seed_companies or end of list.
    6. If under max_seed_companies and segments exhausted, backfill from
       highest-scoring skipped companies.
    7. Flag companies that appeared in last_snapshot as previously_exported=True.
    """
    snap_cfg = config.get("snapshot_config", {})
    max_seeds = snap_cfg.get("max_seed_companies", 10)
    min_score = snap_cfg.get("min_seed_score", 30)
    min_calls = snap_cfg.get("min_calls_for_seed", 2)
    diversify = snap_cfg.get("diversify_segments", True)
    max_per_segment = snap_cfg.get("max_seeds_per_segment", 3)

    # Previous seed company_ids for previously_exported flagging
    prev_seed_ids = set()
    if last_snapshot and "seed_companies" in last_snapshot:
        for s in last_snapshot["seed_companies"]:
            prev_seed_ids.add(s.get("company_id", ""))

    # Filter
    eligible = [
        c for c in scored_companies
        if c.get("score", 0) >= min_score and c.get("call_count", 0) >= min_calls
    ]

    # Sort by score descending
    eligible.sort(key=lambda c: c.get("score", 0), reverse=True)

    seeds = []
    skipped = []
    segment_counts = {}

    if diversify:
        # Pass 1: respect segment caps
        for c in eligible:
            if len(seeds) >= max_seeds:
                break
            seg = c.get("segment", "") or "unknown"
            if segment_counts.get(seg, 0) >= max_per_segment:
                skipped.append(c)
                continue
            segment_counts[seg] = segment_counts.get(seg, 0) + 1
            seeds.append(c)

        # Pass 2: backfill from skipped if under max
        for c in skipped:
            if len(seeds) >= max_seeds:
                break
            seeds.append(c)
    else:
        seeds = eligible[:max_seeds]

    # Flag previously exported — only true if actually pushed to Clay.
    # TODO: track real export history in data/export_history.json.
    # For now, prev_seed_ids only means "was in last snapshot" not "was exported",
    # so we leave all as False until export tracking is built.
    for seed in seeds:
        seed["previously_exported"] = False

    return seeds


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------
def load_last_snapshot():
    """Load the last snapshot from disk."""
    try:
        with open(SNAPSHOT_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_snapshot(snapshot):
    """Atomic write: save snapshot to disk for re-engagement baselines."""
    dir_name = os.path.dirname(SNAPSHOT_PATH)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, SNAPSHOT_PATH)
    except:
        os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# ICP Snapshot generation (new in v3 — the core function)
# ---------------------------------------------------------------------------
def generate_icp_snapshot(data, config=None):
    """
    Generate an ICP Snapshot from dashboard data.

    Orchestrates: company aggregation -> scoring -> segment analysis ->
    feature rankings -> competitor landscape -> seed selection ->
    exclude list -> credit estimation -> re-engagement detection.

    Does NOT push anything to Clay.
    """
    if config is None:
        config = get_config()

    now = datetime.now(timezone.utc)
    snapshot_id = f"icp-{now.strftime('%Y-%m-%d')}"
    seed_set_id = f"seeds-{snapshot_id}"

    # 1. Aggregate companies
    prospects = aggregate_companies(data)

    # 2. Score each company
    scored = []
    for slug, p in prospects.items():
        result = calculate_score(p)
        scored.append({
            **p,
            "company_id": slug,
            "score": result["total"],
            "score_breakdown": result["breakdown"],
        })

    # 3. Segment analysis
    segments_map = {}
    for c in scored:
        seg = c.get("segment", "") or "unknown"
        segments_map.setdefault(seg, []).append(c)

    segments = []
    for seg_name, companies in sorted(segments_map.items()):
        scores = [c["score"] for c in companies]
        all_features = []
        all_competitors = []
        for c in companies:
            all_features.extend(c.get("features_requested", []))
            for comp in c.get("competitors_mentioned", []):
                name = comp.get("competitor", comp.get("name", "")) if isinstance(comp, dict) else comp
                if name:
                    all_competitors.append(name)

        # Count features and competitors
        feature_counts = {}
        for f in all_features:
            feature_counts[f] = feature_counts.get(f, 0) + 1
        comp_counts = {}
        for c in all_competitors:
            comp_counts[c] = comp_counts.get(c, 0) + 1

        segments.append({
            "name": seg_name,
            "company_count": len(companies),
            "avg_calls": round(sum(c.get("call_count", 0) for c in companies) / len(companies), 1) if companies else 0,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "top_features": sorted(feature_counts.keys(), key=lambda f: -feature_counts[f])[:5],
            "top_competitors": sorted(comp_counts.keys(), key=lambda c: -comp_counts[c])[:5],
            "example_companies": [c["company_name"] for c in sorted(companies, key=lambda x: -x["score"])[:5]],
        })

    # 4. Feature rankings
    feature_data = {}
    for c in scored:
        for feat in c.get("features_requested", []):
            if feat not in feature_data:
                feature_data[feat] = {"companies": set(), "segments": set(), "dates": []}
            feature_data[feat]["companies"].add(c["company_name"])
            seg = c.get("segment", "") or "unknown"
            feature_data[feat]["segments"].add(seg)
            if c.get("last_call_date"):
                feature_data[feat]["dates"].append(c["last_call_date"])

    feature_rankings = []
    for feat, fdata in sorted(feature_data.items(), key=lambda x: -len(x[1]["companies"])):
        dates = sorted(fdata["dates"])
        if len(dates) >= 4:
            mid = len(dates) // 2
            trend = "rising" if len(dates[mid:]) > len(dates[:mid]) * 1.5 else (
                "declining" if len(dates[:mid]) > len(dates[mid:]) * 1.5 else "stable"
            )
        else:
            trend = "stable"

        feature_rankings.append({
            "feature": feat,
            "mention_count": len(fdata["companies"]),
            "unique_companies": len(fdata["companies"]),
            "segments": sorted(fdata["segments"]),
            "trend": trend,
        })

    # 5. Competitor landscape
    comp_landscape = {}
    for c in scored:
        for comp_raw in c.get("competitors_mentioned", []):
            comp = comp_raw.get("competitor", comp_raw.get("name", "")) if isinstance(comp_raw, dict) else comp_raw
            if not comp:
                continue
            if comp not in comp_landscape:
                comp_landscape[comp] = {"segments": set(), "count": 0}
            comp_landscape[comp]["count"] += 1
            seg = c.get("segment", "") or "unknown"
            comp_landscape[comp]["segments"].add(seg)

    competitor_landscape = [
        {
            "competitor": name,
            "mention_count": cdata["count"],
            "segments": sorted(cdata["segments"]),
            "context": "",
        }
        for name, cdata in sorted(comp_landscape.items(), key=lambda x: -x[1]["count"])
    ]

    # 6. Seed selection
    last_snapshot = load_last_snapshot()
    seeds = select_seed_companies(scored, config, last_snapshot)

    # 7. Suggested Clay filters
    filter_cfg = config.get("suggested_filter_config", {})
    all_features_flat = [f["feature"] for f in feature_rankings[:5]]
    suggested_filters = {
        "industries": filter_cfg.get("default_industries", []),
        "keywords": filter_cfg.get("default_keywords", []) or all_features_flat,
        "company_sizes": filter_cfg.get("default_company_sizes", ["11-50", "51-200", "201-500"]),
        "exclude_domains": [],  # Populated below
    }

    # 8. Exclude list (companies with calls in last active_days)
    active_days = config.get("exclude_config", {}).get("active_days", 90)
    cutoff = (now - timedelta(days=active_days)).strftime("%Y-%m-%d")
    exclude_domains = []
    for c in scored:
        last_call = c.get("last_call_date", "")
        if last_call and last_call >= cutoff:
            domain = normalize_domain(c.get("domain", ""))
            if domain:
                exclude_domains.append(domain)
                suggested_filters["exclude_domains"].append(domain)

    # 9. Credit impact estimate
    credit_cfg = config.get("credit_controls", {})
    ocean_per_seed = credit_cfg.get("ocean_limit_per_seed", 10)
    seeds_missing_domain = sum(1 for s in seeds if not normalize_domain(s.get("domain", "")))

    ocean = len(seeds) * ocean_per_seed
    domain_lookups = seeds_missing_domain
    tech_stack = round(ocean * credit_cfg.get("expected_pass_rate", 0.35))
    decision_makers = round(tech_stack * credit_cfg.get("dm_search_rate", 0.8))
    contacts = round(decision_makers * credit_cfg.get("dm_found_rate", 0.6))
    total_credits = ocean + domain_lookups + tech_stack + decision_makers + contacts
    budget = credit_cfg.get("monthly_credit_budget", 2000)

    credit_impact = {
        "seed_count": len(seeds),
        "stages": {
            "ocean_lookalikes": ocean,
            "domain_lookups": domain_lookups,
            "tech_stack": tech_stack,
            "decision_makers": decision_makers,
            "contact_enrichment": contacts,
        },
        "total_estimated": total_credits,
        "assumptions": {
            "ocean_limit_per_seed": ocean_per_seed,
            "expected_pass_rate": credit_cfg.get("expected_pass_rate", 0.35),
            "dm_search_rate": credit_cfg.get("dm_search_rate", 0.8),
            "dm_found_rate": credit_cfg.get("dm_found_rate", 0.6),
        },
        "warning": f"High: estimated {total_credits}+ credits" if total_credits > budget else None,
    }

    # 10. Scoring config hash
    scoring_hash = generate_scoring_config_hash(config)

    # 11. Re-engagement detection
    re_engagement_alerts = []
    re_cfg = config.get("re_engagement", {})
    if re_cfg.get("enabled", True) and last_snapshot:
        # Check if scoring config changed
        prev_hash = last_snapshot.get("scoring_config_hash", "")
        suppress = re_cfg.get("suppress_on_config_change", True) and prev_hash and prev_hash != scoring_hash

        if suppress:
            logger.info("Re-engagement suppressed: scoring config hash changed")
        else:
            # Build previous score lookup
            prev_scores = {}
            for s in last_snapshot.get("seed_companies", []):
                prev_scores[s.get("company_id", "")] = s.get("score", 0)
            # Also check all scored companies from previous snapshot
            for c in last_snapshot.get("_all_scored", []):
                prev_scores[c.get("company_id", "")] = c.get("score", 0)

            threshold = re_cfg.get("score_delta_threshold", 20)
            for c in scored:
                cid = c.get("company_id", "")
                if cid in prev_scores:
                    delta = c["score"] - prev_scores[cid]
                    if delta >= threshold:
                        re_engagement_alerts.append({
                            "company_name": c["company_name"],
                            "company_id": cid,
                            "prospect_id": c.get("prospect_id", cid),
                            "previous_score": prev_scores[cid],
                            "current_score": c["score"],
                            "score_delta": delta,
                            "reason": f"Score increased by {delta} points",
                            "idempotency_key": generate_idempotency_key("re_engagement", company_id=cid, now=now),
                        })

    # 11.5. Pre-compute exclude payloads and embed in snapshot
    exclude_payloads = []
    exclude_omitted = []
    for c in scored:
        last_call = c.get("last_call_date", "")
        if not last_call or last_call < cutoff:
            continue
        domain = normalize_domain(c.get("domain", ""))
        linkedin = c.get("linkedin_url", "")
        if not domain and not linkedin:
            exclude_omitted.append(c["company_name"])
            continue
        exclude_payloads.append({
            "normalized_domain": domain,
            "linkedin_url": linkedin or None,
            "company_name": c["company_name"],
            "last_call_date": last_call,
            "idempotency_key": generate_idempotency_key(
                "exclude",
                normalized_domain=domain,
                linkedin_url=linkedin,
            ),
        })

    # Build snapshot
    snapshot = {
        "snapshot_id": snapshot_id,
        "generated_at": now.isoformat(),
        "data_generated_at": data.get("stats", {}).get("generated", ""),
        "source": "sales_intelligence_dashboard",
        "segments": segments,
        "feature_rankings": feature_rankings,
        "competitor_landscape": competitor_landscape,
        "seed_companies": [
            {
                "company_name": s["company_name"],
                "company_id": s["company_id"],
                "domain": s.get("domain", ""),
                "domain_confidence": s.get("domain_confidence", "unresolved"),
                "normalized_domain": normalize_domain(s.get("domain", "")),
                "segment": s.get("segment", ""),
                "score": s["score"],
                "call_count": s.get("call_count", 0),
                "features_requested": s.get("features_requested", []),
                "competitors_mentioned": s.get("competitors_mentioned", []),
                "previously_exported": s.get("previously_exported", False),
            }
            for s in seeds
        ],
        "seed_set_id": seed_set_id,
        "suggested_filters": suggested_filters,
        "estimated_credit_impact": credit_impact,
        "scoring_config_hash": scoring_hash,
        "re_engagement_alerts": re_engagement_alerts,
        "exclude_list": exclude_payloads,
        "exclude_omitted": exclude_omitted,
        # Internal: full scored list for next re-engagement comparison
        "_all_scored": [
            {"company_id": c["company_id"], "score": c["score"]}
            for c in scored
        ],
    }

    # 12. Save snapshot for next comparison
    save_snapshot(snapshot)

    return snapshot


# ---------------------------------------------------------------------------
# Payload generators (new in v3)
# ---------------------------------------------------------------------------
def generate_seed_payloads(snapshot):
    """Convert snapshot seed companies into webhook-ready payloads."""
    now_iso = datetime.now(timezone.utc).isoformat()
    seed_set_id = snapshot.get("seed_set_id", "")
    payloads = []

    for seed in snapshot.get("seed_companies", []):
        domain = normalize_domain(seed.get("domain", ""))
        payload = {
            "idempotency_key": generate_idempotency_key(
                "seed",
                seed_set_id=seed_set_id,
                normalized_domain=domain,
                company_id=seed.get("company_id", ""),
            ),
            "seed_set_id": seed_set_id,
            "company_name": seed.get("company_name", ""),
            "company_id": seed.get("company_id", ""),
            "domain": seed.get("domain", ""),
            "normalized_domain": domain,
            "segment": seed.get("segment", ""),
            "priority_score": seed.get("score", 0),
            "features_requested_csv": _make_csv(seed.get("features_requested", [])),
            "competitors_csv": _make_csv(seed.get("competitors_mentioned", [])),
            "call_count": seed.get("call_count", 0),
            "source": "sales_intelligence_dashboard",
            "exported_at": now_iso,
        }
        payloads.append(payload)

    return payloads


def generate_exclude_payloads(data, config=None):
    """
    Generate exclude list payloads from dashboard data.
    Only includes companies with calls in last active_days.
    """
    if config is None:
        config = get_config()

    active_days = config.get("exclude_config", {}).get("active_days", 90)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=active_days)).strftime("%Y-%m-%d")

    prospects = aggregate_companies(data)
    payloads = []
    omitted = []

    for slug, p in prospects.items():
        last_call = p.get("last_call_date", "")
        if not last_call or last_call < cutoff:
            continue

        domain = normalize_domain(p.get("domain", ""))
        linkedin = p.get("linkedin_url", "")

        if not domain and not linkedin:
            omitted.append(p["company_name"])
            logger.warning("Exclude list: omitting '%s' — no domain or LinkedIn URL", p["company_name"])
            continue

        payload = {
            "normalized_domain": domain,
            "linkedin_url": linkedin or None,
            "company_name": p["company_name"],
            "last_call_date": last_call,
            "idempotency_key": generate_idempotency_key(
                "exclude",
                normalized_domain=domain,
                linkedin_url=linkedin,
            ),
        }
        payloads.append(payload)

    return payloads, omitted


def generate_re_engagement_payloads(snapshot):
    """Convert snapshot re-engagement alerts into webhook-ready payloads."""
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []

    for alert in snapshot.get("re_engagement_alerts", []):
        payload = {
            "company_name": alert["company_name"],
            "company_id": alert["company_id"],
            "prospect_id": alert.get("prospect_id", alert["company_id"]),
            "previous_score": alert["previous_score"],
            "current_score": alert["current_score"],
            "score_delta": alert["score_delta"],
            "reason": alert["reason"],
            "idempotency_key": alert["idempotency_key"],
            "source": "sales_intelligence_dashboard",
            "exported_at": now_iso,
        }
        payloads.append(payload)

    return payloads
