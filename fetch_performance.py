#!/usr/bin/env python3
"""
Fetch performance data from Mixmax, Fireflies, and HubSpot.

Pulls email activity, recorded calls, meetings, and pipeline data,
computes daily buckets and period slices, and writes performance.json.

Usage:
    python3 fetch_performance.py [--days 90] [--out test_output/performance.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MIXMAX_BASE = "https://api.mixmax.com/v1"
HUBSPOT_BASE = "https://api.hubspot.com"
MIXMAX_RECORD_CAP = 5000


def collect_mixmax_tokens() -> list[tuple[str, str]]:
    """Return list of (email, token) for all configured Mixmax tokens."""
    candidates = [
        ("zach.mccall@teachable.com",   os.getenv("MIXMAX_API_TOKEN", "")),
        ("jerome.olaloye@teachable.com", os.getenv("MIXMAX_API_TOKEN_JEROME", "")),
        ("kevin.codde@teachable.com",    os.getenv("MIXMAX_API_TOKEN_KEVIN", "")),
    ]
    return [(email, token) for email, token in candidates if token.strip()]


# Internal reps — canonical emails and speaker name mapping
INTERNAL_REP_EMAILS = {
    "zach.mccall@teachable.com",
    "jerome.olaloye@teachable.com",
    "kevin.codde@teachable.com",
}

SPEAKER_NAME_TO_EMAIL = {
    "zach mccall": "zach.mccall@teachable.com",
    "zach": "zach.mccall@teachable.com",
    "jerome olaloye": "jerome.olaloye@teachable.com",
    "jerome": "jerome.olaloye@teachable.com",
    "kevin codde": "kevin.codde@teachable.com",
    "kevin": "kevin.codde@teachable.com",
}

INTERNAL_SPEAKER_NAMES = set(SPEAKER_NAME_TO_EMAIL.keys()) | {
    "lennie zhu", "sarah dean",
    "jonathan corvin-blackburn", "jonathan corvin blackburn",
    "luke easley",
}


# ---------------------------------------------------------------------------
# Mixmax pull
# ---------------------------------------------------------------------------

def pull_mixmax_livefeed(token: str, since: datetime) -> tuple[list[dict], bool]:
    """Pull livefeed messages from Mixmax. Returns (messages, truncated)."""
    messages = []
    truncated = False
    params = {
        "apiToken": token,
        "startDate": since.isoformat(),
    }

    while True:
        resp = requests.get(f"{MIXMAX_BASE}/livefeed", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or data if isinstance(data, list) else data.get("results", [])
        if isinstance(results, list):
            messages.extend(results)

        if len(messages) >= MIXMAX_RECORD_CAP:
            truncated = True
            messages = messages[:MIXMAX_RECORD_CAP]
            break

        if data.get("hasNext") and data.get("next"):
            params["next"] = data["next"]
        else:
            break

    logger.info("Mixmax livefeed: %d messages%s", len(messages), " (TRUNCATED)" if truncated else "")
    return messages, truncated


def pull_mixmax_sequences(token: str) -> list[dict]:
    """Pull sequences from Mixmax."""
    resp = requests.get(
        f"{MIXMAX_BASE}/sequences",
        headers={"X-API-Token": token},
        params={"limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    seqs = data.get("results") or data if isinstance(data, list) else data.get("results", [])
    logger.info("Mixmax sequences: %d", len(seqs))
    return seqs


def pull_all_mixmax_messages(
    tokens: list[tuple[str, str]],
    since: datetime,
) -> tuple[list[dict], bool]:
    """Pull livefeed from every configured Mixmax token and merge results."""
    all_messages: list[dict] = []
    any_truncated = False

    for rep_email, token in tokens:
        logger.info("Mixmax: pulling livefeed for %s", rep_email)
        try:
            msgs, truncated = pull_mixmax_livefeed(token, since)
            all_messages.extend(msgs)
            if truncated:
                any_truncated = True
                logger.warning(
                    "Mixmax livefeed truncated at %d records for %s",
                    MIXMAX_RECORD_CAP, rep_email,
                )
        except Exception as e:
            logger.error("Mixmax livefeed failed for %s: %s", rep_email, e)

    logger.info(
        "Mixmax: %d total messages across %d token(s)",
        len(all_messages), len(tokens),
    )
    return all_messages, any_truncated


# ---------------------------------------------------------------------------
# Fireflies pull
# ---------------------------------------------------------------------------

def pull_fireflies_calls(api_key: str, since: datetime, until: datetime) -> list[dict]:
    """Pull calls from Fireflies within the window."""
    from client import FirefliesRetriever
    from models import CallFilter

    retriever = FirefliesRetriever(api_key)
    days_back = (datetime.now(UTC) - since).days + 1
    filt = CallFilter(
        days_back=days_back,
        limit=500,
        title_keywords=["teachable", "followup", "follow-up", "follow up",
                         "zach mccall", "jerome olaloye", "kevin codde"],
        owner_emails=list(INTERNAL_REP_EMAILS),
        bypass_keywords_owners=["kevin.codde"],
    )
    calls = retriever.get_calls(filter_criteria=filt, verbose=False)
    # Filter to window
    result = []
    for c in calls:
        try:
            call_dt = datetime.fromisoformat(c.date.replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue
        if since.astimezone(ET) <= call_dt <= until.astimezone(ET):
            result.append(c)
    logger.info("Fireflies: %d calls in window", len(result))
    return result


def resolve_call_owner(call, rep_emails: set) -> str | None:
    """Determine which rep owns a call. Returns email or None."""
    # 1. organizer_email if internal
    if call.organizer_email and call.organizer_email.lower() in rep_emails:
        return call.organizer_email.lower()
    # 2. First matched internal attendee
    for att in (call.attendees or []):
        email = (att.get("email") or "").lower()
        if email in rep_emails:
            return email
    # 3. First matched internal speaker from transcript
    for sentence in (call.sentences or []):
        speaker = (sentence.get("speaker_name") or "").lower().strip()
        if speaker in SPEAKER_NAME_TO_EMAIL:
            return SPEAKER_NAME_TO_EMAIL[speaker]
    return None


# ---------------------------------------------------------------------------
# HubSpot pull
# ---------------------------------------------------------------------------

def _hs_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def pull_hubspot_owners(token: str) -> dict[str, dict]:
    """Pull owners. Returns {owner_id: {name, email}}."""
    resp = requests.get(
        f"{HUBSPOT_BASE}/crm/v3/owners",
        headers=_hs_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    owners = {}
    for o in resp.json().get("results", []):
        oid = str(o.get("id", ""))
        email = (o.get("email") or "").lower()
        first = o.get("firstName", "")
        last = o.get("lastName", "")
        owners[oid] = {"name": f"{first} {last}".strip(), "email": email}
    logger.info("HubSpot owners: %d", len(owners))
    return owners


def pull_hubspot_pipeline_stages(token: str) -> tuple[dict[str, str], list[dict]]:
    """Pull deal pipeline stages.

    Returns (stage_map, pipelines) where stage_map is {stage_id: label}
    and pipelines is [{id, label, stage_ids: [ordered stage IDs]}].
    """
    resp = requests.get(
        f"{HUBSPOT_BASE}/crm/v3/pipelines/deals",
        headers=_hs_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    stages = {}
    pipelines = []
    for pipeline in resp.json().get("results", []):
        pid = pipeline.get("id", "")
        plabel = pipeline.get("label", pid)
        ordered_stages = sorted(pipeline.get("stages", []),
                                key=lambda s: s.get("displayOrder", 0))
        stage_ids = []
        for stage in ordered_stages:
            sid = stage.get("id") or stage.get("stageId", "")
            stages[sid] = stage.get("label", sid)
            stage_ids.append(sid)
        pipelines.append({"id": pid, "label": plabel, "stage_ids": stage_ids})
    logger.info("HubSpot pipeline stages: %d across %d pipelines", len(stages), len(pipelines))
    return stages, pipelines


def _hs_search_all(token: str, object_type: str, body: dict) -> list[dict]:
    """Paginate through HubSpot search results."""
    results = []
    after = None
    while True:
        req = {**body}
        if after:
            req["after"] = after
        resp = requests.post(
            f"{HUBSPOT_BASE}/crm/v3/objects/{object_type}/search",
            headers=_hs_headers(token),
            json=req,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        paging = data.get("paging", {})
        nxt = paging.get("next", {}).get("after")
        if nxt:
            after = nxt
        else:
            break
    return results


def pull_hubspot_deals(token: str, since: datetime) -> list[dict]:
    """Pull deals created since `since`."""
    body = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "createdate",
                "operator": "GTE",
                "value": str(int(since.timestamp() * 1000)),
            }]
        }],
        "properties": [
            "dealname", "amount", "dealstage", "closedate",
            "hubspot_owner_id", "pipeline", "createdate",
            "hs_v2_date_entered_current_stage",
        ],
        "limit": 100,
    }
    deals = _hs_search_all(token, "deals", body)
    logger.info("HubSpot deals: %d", len(deals))
    return deals


def pull_hubspot_meetings(token: str, since: datetime) -> list[dict]:
    """Pull meetings booked (created) since `since`.

    Uses hs_createdate (booking time) not hs_timestamp (occurrence time)
    per spec: Meetings Booked KPI counts when meetings were scheduled.
    """
    body = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "hs_createdate",
                "operator": "GTE",
                "value": str(int(since.timestamp() * 1000)),
            }]
        }],
        "properties": [
            "hs_meeting_title", "hs_createdate", "hs_timestamp",
            "hubspot_owner_id", "hs_meeting_outcome",
        ],
        "limit": 100,
    }
    meetings = _hs_search_all(token, "meetings", body)
    # Filter out sample records
    sample_count = 0
    filtered = []
    for m in meetings:
        title = (m.get("properties", {}).get("hs_meeting_title") or "")
        if title.startswith("(Sample"):
            sample_count += 1
            continue
        filtered.append(m)
    logger.info("HubSpot meetings: %d (filtered %d samples)", len(filtered), sample_count)
    return filtered


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _to_et_date(ts_str: str | int | None) -> date | None:
    """Parse an ISO timestamp or ms epoch and return the ET date."""
    if ts_str is None:
        return None
    # Numeric epoch (ms)
    if isinstance(ts_str, (int, float)):
        ms = int(ts_str)
        if ms == 0:
            return None
        dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
        return dt.astimezone(ET).date()
    if not ts_str:
        return None
    try:
        # Try ISO format
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone(ET).date()
    except (ValueError, TypeError):
        pass
    try:
        # Try millisecond epoch as string (HubSpot)
        ms = int(ts_str)
        dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
        return dt.astimezone(ET).date()
    except (ValueError, TypeError):
        return None


def _date_range(start: date, end: date) -> list[date]:
    """Generate list of dates from start to end inclusive."""
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def _period_bounds(today: date) -> dict[str, tuple[date, date]]:
    """Compute start/end for each period key."""
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    return {
        "7d": (today - timedelta(days=6), today),
        "30d": (today - timedelta(days=29), today),
        "90d": (today - timedelta(days=89), today),
        "this_month": (first_of_month, today),
        "last_month": (last_month_start, last_month_end),
    }


def _prior_period(key: str, today: date) -> tuple[date, date]:
    """Compute the comparison (prior) period for delta chips."""
    if key == "7d":
        return (today - timedelta(days=13), today - timedelta(days=7))
    elif key == "30d":
        return (today - timedelta(days=59), today - timedelta(days=30))
    elif key == "90d":
        return (today - timedelta(days=179), today - timedelta(days=90))
    elif key == "this_month":
        # Same elapsed days in prior month
        elapsed = today.day
        first_of_month = today.replace(day=1)
        prior_month_end = first_of_month - timedelta(days=1)
        prior_month_start = prior_month_end.replace(day=1)
        prior_end = min(prior_month_start + timedelta(days=elapsed - 1), prior_month_end)
        return (prior_month_start, prior_end)
    elif key == "last_month":
        # Month before last (e.g. if last_month is March, compare against February)
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        prior_end = last_month_start - timedelta(days=1)
        prior_start = prior_end.replace(day=1)
        return (prior_start, prior_end)
    return (today, today)


def _sum_days(day_buckets: dict[date, dict], start: date, end: date, key: str) -> int:
    """Sum a metric across daily buckets for a date range."""
    total = 0
    d = start
    while d <= end:
        total += day_buckets.get(d, {}).get(key, 0)
        d += timedelta(days=1)
    return total


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

TARGET_METRICS = ["emails_sent", "meetings_booked", "deals_created", "recorded_calls"]


def load_targets(targets_path: str | None = None) -> dict:
    """Load performance_targets.json. Returns empty structure if missing."""
    if targets_path is None:
        targets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "performance_targets.json")
    if not os.path.exists(targets_path):
        return {"targets": [], "stage_thresholds": {}}
    with open(targets_path) as f:
        return json.load(f)


def inject_targets(rep_entry: dict, targets: list) -> dict:
    """Add *_target, *_pct, and attainment_pct to a rep slice entry."""
    rep_email = rep_entry.get("email", "")
    target_row = next((t for t in targets if t.get("rep_email") == rep_email), None)

    attainment_values = []
    for metric in TARGET_METRICS:
        target_val = target_row.get(metric) if target_row else None
        actual_val = rep_entry.get(metric, 0) or 0
        rep_entry[f"{metric}_target"] = target_val
        if target_val and target_val > 0:
            pct = round(actual_val / target_val, 4)
            rep_entry[f"{metric}_pct"] = pct
            attainment_values.append(pct)
        else:
            rep_entry[f"{metric}_pct"] = None

    rep_entry["attainment_pct"] = (
        round(sum(attainment_values) / len(attainment_values), 4)
        if attainment_values else None
    )
    return rep_entry


# ---------------------------------------------------------------------------
# Stalled deal detection
# ---------------------------------------------------------------------------

def days_in_current_stage(deal: dict, today: date) -> int | None:
    """Compute days the deal has been in its current stage."""
    date_str = deal.get("properties", {}).get("hs_v2_date_entered_current_stage")
    if not date_str:
        return None
    try:
        entered = datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).astimezone(ET)
        return (today - entered.date()).days
    except (ValueError, TypeError):
        return None


def is_stalled(deal: dict, thresholds: dict, today: date) -> bool:
    """Check if a deal has exceeded its stage threshold."""
    stage_id = deal.get("properties", {}).get("dealstage", "")
    threshold = thresholds.get(stage_id, 14)
    days = days_in_current_stage(deal, today)
    return days is not None and days > threshold


def build_stalled_deals_list(
    all_open_deals: list, thresholds: dict, today: date,
    stage_labels: dict, owner_map: dict, owner_id_to_email: dict,
    stage_to_pipeline_label: dict | None = None,
) -> list:
    """All stalled open deals, sorted by days_in_stage desc. No cap."""
    result = []
    for d in all_open_deals:
        if not is_stalled(d, thresholds, today):
            continue
        p = d.get("properties", {})
        stage_id = p.get("dealstage", "")
        owner_id = str(p.get("hubspot_owner_id", ""))
        owner = owner_map.get(owner_id, {})
        result.append({
            "id": d.get("id", ""),
            "name": p.get("dealname", ""),
            "stage_id": stage_id,
            "stage_label": stage_labels.get(stage_id, stage_id),
            "pipeline": (stage_to_pipeline_label or {}).get(stage_id, ""),
            "amount": float(p["amount"]) if p.get("amount") else None,
            "days_in_stage": days_in_current_stage(d, today),
            "rep_name": owner.get("name", owner_id),
            "rep_email": owner.get("email", ""),
        })
    result.sort(key=lambda d: d.get("days_in_stage") or 0, reverse=True)
    return result


def compute_avg_days_by_stage(
    all_open_deals: list, today: date,
) -> dict[str, float]:
    """Average days in current stage for each stage with open deals."""
    stage_days: dict[str, list] = defaultdict(list)
    for d in all_open_deals:
        stage_id = d.get("properties", {}).get("dealstage", "")
        days = days_in_current_stage(d, today)
        if days is not None:
            stage_days[stage_id].append(days)
    return {
        sid: round(sum(vals) / len(vals), 1)
        for sid, vals in stage_days.items()
        if vals
    }


# ---------------------------------------------------------------------------
# Per-rep detail for drawer
# ---------------------------------------------------------------------------

def build_rep_detail(
    rep_email: str, owner_id: str,
    all_deals: list, all_calls: list, all_meetings: list,
    thresholds: dict, today: date,
    stage_labels: dict, pipeline_labels: dict,
    won_lost_ids: set, owner_id_to_email: dict,
    call_owner_func,
) -> dict:
    """Build detailed data for a single rep's drilldown drawer."""
    # Open deals only, sorted by createdate desc, cap 30
    rep_open_deals = []
    for d in all_deals:
        p = d.get("properties", {})
        if str(p.get("hubspot_owner_id", "")) != str(owner_id):
            continue
        if p.get("dealstage", "") in won_lost_ids:
            continue
        rep_open_deals.append(d)
    rep_open_deals.sort(
        key=lambda d: d.get("properties", {}).get("createdate", ""), reverse=True
    )

    # Calls, date desc, cap 30
    rep_calls = [c for c in all_calls if call_owner_func(c) == rep_email]
    rep_calls.sort(key=lambda c: getattr(c, "date", "") or "", reverse=True)
    recent_calls = []
    for c in rep_calls[:30]:
        recent_calls.append({
            "id": getattr(c, "id", ""),
            "title": getattr(c, "title", ""),
            "date": (getattr(c, "date", "") or "")[:10],
            "duration_min": round(c.duration_minutes) if hasattr(c, "duration_minutes") else 0,
            "transcript_url": getattr(c, "transcript_url", None),
        })

    # Meetings, hs_createdate desc, cap 30
    rep_meetings = [
        m for m in all_meetings
        if str(m.get("properties", {}).get("hubspot_owner_id", "")) == str(owner_id)
    ]
    rep_meetings.sort(
        key=lambda m: m.get("properties", {}).get("hs_createdate", ""), reverse=True
    )
    recent_meetings = []
    for m in rep_meetings[:30]:
        p = m.get("properties", {})
        recent_meetings.append({
            "id": m.get("id", ""),
            "title": p.get("hs_meeting_title", ""),
            "hs_createdate": (p.get("hs_createdate") or "")[:10],
            "outcome": p.get("hs_meeting_outcome"),
        })

    # Uncapped open_deals for pipeline math (stage counts, popovers)
    open_deals = []
    for d in rep_open_deals:
        p = d.get("properties", {})
        stage_id = p.get("dealstage", "")
        open_deals.append({
            "id": d.get("id", ""),
            "name": p.get("dealname", ""),
            "stage_id": stage_id,
            "stage_label": stage_labels.get(stage_id, stage_id),
            "pipeline": pipeline_labels.get(p.get("pipeline", ""), ""),
            "amount": float(p["amount"]) if p.get("amount") else None,
            "createdate": (p.get("createdate") or "")[:10],
            "days_in_stage": days_in_current_stage(d, today),
            "stalled": is_stalled(d, thresholds, today),
        })

    return {
        "window_days": 90,
        "open_deals": open_deals,
        "recent_calls": recent_calls,
        "recent_meetings": recent_meetings,
    }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def build_performance_data(days: int = 90) -> dict:
    """Pull all sources and build the performance JSON."""
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

    mixmax_tokens = collect_mixmax_tokens()
    fireflies_key = os.getenv("FIREFLIES_API_KEY", "")
    hubspot_token = os.getenv("HUBSPOT_TOKEN", "")

    # Load targets (Path B — file-based, no HubSpot dependency)
    targets_data = load_targets()
    target_list = targets_data.get("targets", [])
    stage_thresholds = targets_data.get("stage_thresholds", {})

    now_et = datetime.now(ET)
    today = now_et.date()

    # Enforce minimum so all_dates always covers the widest slice + its prior period.
    # The 90d prior period starts 179 days back; anything less silently undercounts.
    MIN_DAYS = 90
    if days < MIN_DAYS:
        logger.warning(
            "--days %d is less than minimum %d; clamping to %d to avoid "
            "silent undercount in 90d slice and prior-period comparisons.",
            days, MIN_DAYS, MIN_DAYS,
        )
        days = MIN_DAYS

    window_start = today - timedelta(days=days - 1)
    # For prior period comparisons we need data going further back
    extended_start = today - timedelta(days=days * 2)
    since_utc = datetime.combine(extended_start, datetime.min.time(), tzinfo=ET).astimezone(UTC)
    until_utc = datetime.combine(today, datetime.max.time().replace(microsecond=0), tzinfo=ET).astimezone(UTC)

    honesty = {
        "mixmax_truncated": False,
        "mixmax_record_count": 0,
        "mixmax_token_count": 0,
        "unassigned_rep_count": 0,
        "fireflies_call_count": 0,
        "hubspot_sample_meetings_filtered": 0,
        "deals_missing_amount": 0,
        "days_used": days,
    }

    # ------------------------------------------------------------------
    # Pull data from all sources
    # ------------------------------------------------------------------

    # Mixmax
    mm_messages = []
    mm_sequences_raw = []
    if mixmax_tokens:
        mm_messages, truncated = pull_all_mixmax_messages(mixmax_tokens, since_utc)
        honesty["mixmax_truncated"] = truncated
        honesty["mixmax_record_count"] = len(mm_messages)
        honesty["mixmax_token_count"] = len(mixmax_tokens)
        try:
            mm_sequences_raw = pull_mixmax_sequences(mixmax_tokens[0][1])
        except Exception as e:
            logger.error("Mixmax sequences pull failed: %s", e)
    else:
        logger.warning(
            "No Mixmax tokens configured. "
            "Set MIXMAX_API_TOKEN (and optionally MIXMAX_API_TOKEN_JEROME, "
            "MIXMAX_API_TOKEN_KEVIN) in .env"
        )

    # Fireflies
    ff_calls = []
    if fireflies_key:
        try:
            ff_calls = pull_fireflies_calls(fireflies_key, since_utc, until_utc)
            honesty["fireflies_call_count"] = len(ff_calls)
        except Exception as e:
            logger.error("Fireflies pull failed: %s", e)
    else:
        logger.warning("FIREFLIES_API_KEY not set — skipping Fireflies")

    # HubSpot
    hs_owners = {}
    hs_stages = {}
    hs_pipelines = []
    hs_deals = []
    hs_meetings = []
    if hubspot_token:
        try:
            hs_owners = pull_hubspot_owners(hubspot_token)
        except Exception as e:
            logger.error("HubSpot owners pull failed: %s", e)
        try:
            hs_stages, hs_pipelines = pull_hubspot_pipeline_stages(hubspot_token)
        except Exception as e:
            logger.error("HubSpot pipeline stages pull failed: %s", e)
        try:
            hs_deals = pull_hubspot_deals(hubspot_token, since_utc)
        except Exception as e:
            logger.error("HubSpot deals pull failed: %s", e)
        try:
            hs_meetings = pull_hubspot_meetings(hubspot_token, since_utc)
        except Exception as e:
            logger.error("HubSpot meetings pull failed: %s", e)
    else:
        logger.warning("HUBSPOT_TOKEN not set — skipping HubSpot")

    # Build owner lookup from HubSpot
    owner_id_to_email = {oid: info["email"] for oid, info in hs_owners.items()}
    owner_email_to_name = {info["email"]: info["name"] for info in hs_owners.values()}
    # Include known reps even if not in HubSpot owners
    for email in INTERNAL_REP_EMAILS:
        if email not in owner_email_to_name:
            name_parts = email.split("@")[0].split(".")
            owner_email_to_name[email] = " ".join(p.title() for p in name_parts)

    all_rep_emails = set(owner_email_to_name.keys()) | INTERNAL_REP_EMAILS

    # ------------------------------------------------------------------
    # Daily buckets
    # ------------------------------------------------------------------
    all_dates = _date_range(extended_start, today)
    # Team daily
    team_daily: dict[date, dict] = {d: defaultdict(int) for d in all_dates}
    # Per-rep daily
    rep_daily: dict[str, dict[date, dict]] = defaultdict(lambda: {d: defaultdict(int) for d in all_dates})
    # Track email-level data for open rate computation
    email_tracking: dict[date, dict[str, list]] = {d: defaultdict(list) for d in all_dates}

    # --- Process Mixmax messages ---
    for msg in mm_messages:
        sent_ts = msg.get("sent") or msg.get("_id", "")
        d = _to_et_date(sent_ts)
        if not d or d < extended_start or d > today:
            continue
        from_email = (msg.get("fromEmail") or "").lower()
        rep = from_email if from_email in all_rep_emails else "unassigned"

        team_daily[d]["emails_sent"] += 1
        rep_daily[rep][d]["emails_sent"] += 1

        was_tracked = msg.get("wasDeliveredWithTrackedOpens", False)
        num_opens = msg.get("numOpens", 0) or 0
        was_replied = msg.get("wasReplied", False)
        was_bounced = msg.get("wasBounced", False)

        if was_tracked:
            team_daily[d]["emails_tracked"] += 1
            rep_daily[rep][d]["emails_tracked"] += 1
            if num_opens > 0:
                team_daily[d]["emails_opened"] += 1
                rep_daily[rep][d]["emails_opened"] += 1

        if was_replied:
            team_daily[d]["replies"] += 1
            rep_daily[rep][d]["replies"] += 1
        if was_bounced:
            team_daily[d]["bounces"] += 1
            rep_daily[rep][d]["bounces"] += 1

    # --- Process Fireflies calls ---
    for call in ff_calls:
        d = _to_et_date(call.date)
        if not d or d < extended_start or d > today:
            continue
        owner = resolve_call_owner(call, all_rep_emails) or "unassigned"

        team_daily[d]["recorded_calls"] += 1
        rep_daily[owner][d]["recorded_calls"] += 1

        has_transcript = bool(call.full_transcript_text and len(call.full_transcript_text) > 50)
        if has_transcript:
            team_daily[d]["calls_with_transcript"] += 1
            rep_daily[owner][d]["calls_with_transcript"] += 1

        duration_min = call.duration / 60 if call.duration else 0
        team_daily[d]["call_duration_min"] += duration_min
        rep_daily[owner][d]["call_duration_min"] += duration_min

    # --- Process HubSpot meetings (bucketed by hs_createdate = booking time) ---
    for mtg in hs_meetings:
        props = mtg.get("properties", {})
        ts = props.get("hs_createdate") or props.get("hs_timestamp")
        d = _to_et_date(ts)
        if not d or d < extended_start or d > today:
            continue
        owner_id = props.get("hubspot_owner_id", "")
        owner_email = owner_id_to_email.get(str(owner_id), "unassigned")

        team_daily[d]["meetings_booked"] += 1
        rep_daily[owner_email][d]["meetings_booked"] += 1

    # --- Process HubSpot deals ---
    deals_missing_amount = 0
    for deal in hs_deals:
        props = deal.get("properties", {})
        amount = props.get("amount")
        if not amount:
            deals_missing_amount += 1

        # Deals created (flow metric by createdate)
        create_d = _to_et_date(props.get("createdate"))
        owner_id = props.get("hubspot_owner_id", "")
        owner_email = owner_id_to_email.get(str(owner_id), "unassigned")

        if create_d and extended_start <= create_d <= today:
            team_daily[create_d]["deals_created"] += 1
            rep_daily[owner_email][create_d]["deals_created"] += 1

        # Deals won (flow metric by closedate, only WON stage)
        stage_id = props.get("dealstage", "")
        stage_label = hs_stages.get(stage_id, "").upper()
        close_d = _to_et_date(props.get("closedate"))

        if stage_label == "WON" and close_d and extended_start <= close_d <= today:
            team_daily[close_d]["deals_won"] += 1
            rep_daily[owner_email][close_d]["deals_won"] += 1

    honesty["deals_missing_amount"] = deals_missing_amount

    # Count unassigned
    unassigned_count = 0
    for d in all_dates:
        for key in ["emails_sent", "recorded_calls", "meetings_booked"]:
            unassigned_count += rep_daily.get("unassigned", {}).get(d, {}).get(key, 0)
    honesty["unassigned_rep_count"] = unassigned_count

    # ------------------------------------------------------------------
    # Open Pipeline (stock metric, as-of-now) — grouped by pipeline
    # ------------------------------------------------------------------
    # Build a map: pipeline_id -> {stage_ids set}
    pipeline_stage_map: dict[str, set] = {}
    pipeline_label_map: dict[str, str] = {}
    pipeline_stage_order: dict[str, list[str]] = {}
    for pinfo in hs_pipelines:
        pid = pinfo["id"]
        pipeline_stage_map[pid] = set(pinfo["stage_ids"])
        pipeline_label_map[pid] = pinfo["label"]
        pipeline_stage_order[pid] = pinfo["stage_ids"]

    # Reverse lookup: stage_id -> pipeline_id
    stage_to_pipeline: dict[str, str] = {}
    for pid, sids in pipeline_stage_map.items():
        for sid in sids:
            stage_to_pipeline[sid] = pid

    open_pipeline: dict = {"total_value": 0, "deal_count": 0,
                           "as_of": now_et.isoformat(), "by_pipeline": [], "by_rep": []}
    # Per-pipeline stage counts
    pipe_stage_counts: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"count": 0, "value": 0}))
    pipe_totals: dict[str, dict] = defaultdict(lambda: {"count": 0, "value": 0})
    rep_pipeline_totals: dict[str, dict] = defaultdict(lambda: {"count": 0, "value": 0})

    won_lost_labels = {"WON", "LOST"}
    for deal in hs_deals:
        props = deal.get("properties", {})
        stage_id = props.get("dealstage", "")
        stage_label = hs_stages.get(stage_id, "").upper()
        if stage_label in won_lost_labels:
            continue
        amount = 0
        try:
            amount = float(props.get("amount") or 0)
        except (ValueError, TypeError):
            pass
        owner_id = props.get("hubspot_owner_id", "")
        owner_email = owner_id_to_email.get(str(owner_id), "unassigned")
        pipeline_id = props.get("pipeline") or stage_to_pipeline.get(stage_id, "unknown")

        open_pipeline["total_value"] += amount
        open_pipeline["deal_count"] += 1
        pipe_stage_counts[pipeline_id][stage_id]["count"] += 1
        pipe_stage_counts[pipeline_id][stage_id]["value"] += amount
        pipe_totals[pipeline_id]["count"] += 1
        pipe_totals[pipeline_id]["value"] += amount
        rep_pipeline_totals[owner_email]["count"] += 1
        rep_pipeline_totals[owner_email]["value"] += amount

    # Build by_pipeline array (ordered by pipeline definition order)
    for pinfo in hs_pipelines:
        pid = pinfo["id"]
        if pid not in pipe_totals:
            continue
        ordered_stages = [sid for sid in pipeline_stage_order.get(pid, [])
                          if hs_stages.get(sid, "").upper() not in won_lost_labels]
        by_stage = []
        for sid in ordered_stages:
            info = pipe_stage_counts[pid].get(sid, {"count": 0, "value": 0})
            if info["count"] > 0:
                by_stage.append({
                    "stage_id": sid,
                    "label": hs_stages.get(sid, sid),
                    "count": info["count"],
                    "value": info["value"],
                    "stalled_count": 0,
                    "stalled_value": 0.0,
                })
        open_pipeline["by_pipeline"].append({
            "pipeline_id": pid,
            "pipeline_label": pipeline_label_map.get(pid, pid),
            "deal_count": pipe_totals[pid]["count"],
            "total_value": pipe_totals[pid]["value"],
            "by_stage": by_stage,
        })

    open_pipeline["by_rep"] = [
        {"email": email, "name": owner_email_to_name.get(email, email),
         "count": info["count"], "value": info["value"]}
        for email, info in sorted(rep_pipeline_totals.items(), key=lambda x: -x[1]["value"])
    ]

    # Build stage_id -> pipeline_label lookup for stalled deals
    stage_to_pipeline_label = {}
    for pinfo in hs_pipelines:
        pid = pinfo["id"]
        plabel = pipeline_label_map.get(pid, pid)
        for sid in pipeline_stage_order.get(pid, []):
            stage_to_pipeline_label[sid] = plabel

    # Collect all open deals for stalled detection
    all_open_deals = [
        d for d in hs_deals
        if hs_stages.get(d.get("properties", {}).get("dealstage", ""), "").upper()
        not in won_lost_labels
    ]

    # Stalled deals
    stalled_list = build_stalled_deals_list(
        all_open_deals, stage_thresholds, today,
        hs_stages, hs_owners, owner_id_to_email,
        stage_to_pipeline_label,
    )
    open_pipeline["stalled_deal_count"] = len(stalled_list)
    open_pipeline["stalled_deal_value"] = sum(d.get("amount") or 0 for d in stalled_list)
    open_pipeline["stalled_deals"] = stalled_list

    # Back-fill stalled counts into by_stage
    stage_stalled = defaultdict(lambda: {"count": 0, "value": 0.0})
    for sd in stalled_list:
        sid = sd.get("stage_id", "")
        stage_stalled[sid]["count"] += 1
        stage_stalled[sid]["value"] += sd.get("amount") or 0
    for pipe in open_pipeline["by_pipeline"]:
        for stage in pipe["by_stage"]:
            ss = stage_stalled.get(stage["stage_id"])
            if ss:
                stage["stalled_count"] = ss["count"]
                stage["stalled_value"] = ss["value"]

    # Avg days by stage
    open_pipeline["avg_days_by_stage"] = compute_avg_days_by_stage(all_open_deals, today)

    # Pipeline labels for by_rep_detail
    pipeline_labels = {p["id"]: p["label"] for p in hs_pipelines}

    # WON/LOST stage IDs for filtering in by_rep_detail
    won_lost_stage_ids = {
        sid for sid, label in hs_stages.items()
        if label.upper() in won_lost_labels
    }

    # ------------------------------------------------------------------
    # Period slices
    # ------------------------------------------------------------------
    periods = _period_bounds(today)
    # Collect active sequences per period from livefeed attribution
    seq_name_map = {s.get("_id", ""): s.get("name", "Unknown") for s in mm_sequences_raw}

    def _compute_slice(period_key: str, p_start: date, p_end: date) -> dict:
        prior_start, prior_end = _prior_period(period_key, today)

        def _sum(key, s=p_start, e=p_end):
            return _sum_days(team_daily, s, e, key)

        def _sum_prior(key):
            return _sum_days(team_daily, prior_start, prior_end, key)

        def _sum_rep(rep, key, s=p_start, e=p_end):
            return _sum_days(rep_daily.get(rep, {}), s, e, key)

        emails_sent = _sum("emails_sent")
        emails_tracked = _sum("emails_tracked")
        emails_opened = _sum("emails_opened")
        tracking_coverage = emails_tracked / emails_sent if emails_sent > 0 else 0
        open_rate = emails_opened / emails_tracked if emails_tracked > 0 and tracking_coverage >= 0.5 else None
        replies = _sum("replies")
        reply_rate = replies / emails_sent if emails_sent > 0 else 0

        recorded_calls = _sum("recorded_calls")
        calls_with_transcript = _sum("calls_with_transcript")
        transcript_coverage = calls_with_transcript / recorded_calls if recorded_calls > 0 else 0
        total_duration = _sum("call_duration_min")
        avg_duration = total_duration / recorded_calls if recorded_calls > 0 else 0

        # Prior values
        emails_sent_prior = _sum_prior("emails_sent")
        emails_tracked_prior = _sum_prior("emails_tracked")
        emails_opened_prior = _sum_prior("emails_opened")
        tc_prior = emails_tracked_prior / emails_sent_prior if emails_sent_prior > 0 else 0
        open_rate_prior = (emails_opened_prior / emails_tracked_prior
                           if emails_tracked_prior > 0 and tc_prior >= 0.5 else None)
        replies_prior = _sum_prior("replies")
        reply_rate_prior = replies_prior / emails_sent_prior if emails_sent_prior > 0 else 0
        recorded_calls_prior = _sum_prior("recorded_calls")

        # Sequences active in period (had messages sent)
        seq_activity: dict[str, dict] = defaultdict(lambda: {"rep": "", "stages": 0, "sent": 0})
        for msg in mm_messages:
            sent_ts = msg.get("sent") or ""
            msg_date = _to_et_date(sent_ts)
            if not msg_date or msg_date < p_start or msg_date > p_end:
                continue
            seq_id = msg.get("sequence") or msg.get("sequenceId") or ""
            if seq_id and seq_id in seq_name_map:
                seq_activity[seq_id]["sent"] += 1
                seq_activity[seq_id]["rep"] = (msg.get("fromEmail") or "").lower()

        # Enrich sequence info
        sequence_list = []
        for sid, info in seq_activity.items():
            raw = next((s for s in mm_sequences_raw if s.get("_id") == sid), {})
            sequence_list.append({
                "name": seq_name_map.get(sid, "Unknown"),
                "rep_email": info["rep"],
                "stages": len(raw.get("stages", [])),
                "sent_in_period": info["sent"],
            })

        # Per-rep breakdown: include reps with activity, pipeline, targets, or tokens
        active_reps = set()
        for rep in rep_daily:
            for d in _date_range(p_start, p_end):
                bucket = rep_daily[rep].get(d, {})
                if any(bucket.get(k, 0) > 0 for k in
                       ["emails_sent", "recorded_calls", "meetings_booked", "deals_created", "deals_won"]):
                    active_reps.add(rep)
                    break

        # Broaden to visible_reps: also include reps with open pipeline, targets, or tokens
        visible_reps = set(active_reps)
        for r in (open_pipeline.get("by_rep") or []):
            if r.get("email") and r["email"] != "unassigned":
                visible_reps.add(r["email"])
        for t in target_list:
            if t.get("rep_email"):
                visible_reps.add(t["rep_email"])
        for email, _tok in mixmax_tokens:
            visible_reps.add(email)

        by_rep = []
        for rep in sorted(visible_reps):
            r_sent = _sum_rep(rep, "emails_sent")
            r_tracked = _sum_rep(rep, "emails_tracked")
            r_opened = _sum_rep(rep, "emails_opened")
            r_tc = r_tracked / r_sent if r_sent > 0 else 0
            r_replies = _sum_rep(rep, "replies")
            r_calls = _sum_rep(rep, "recorded_calls")
            r_transcripts = _sum_rep(rep, "calls_with_transcript")
            r_duration = _sum_rep(rep, "call_duration_min")
            r_meetings = _sum_rep(rep, "meetings_booked")
            r_deals_created = _sum_rep(rep, "deals_created")
            r_deals_won = _sum_rep(rep, "deals_won")

            # Prior values for per-rep deltas
            r_sent_prior = _sum_rep(rep, "emails_sent", prior_start, prior_end)

            by_rep.append({
                "email": rep,
                "name": owner_email_to_name.get(rep, rep),
                "emails_sent": r_sent,
                "emails_sent_prior": r_sent_prior,
                "replies": r_replies,
                "reply_rate": r_replies / r_sent if r_sent > 0 else 0,
                "open_rate": r_opened / r_tracked if r_tracked > 0 and r_tc >= 0.5 else None,
                "tracking_coverage": r_tc,
                "bounces": _sum_rep(rep, "bounces"),
                "recorded_calls": r_calls,
                "calls_with_transcript": r_transcripts,
                "avg_call_duration_min": r_duration / r_calls if r_calls > 0 else 0,
                "meetings_booked": r_meetings,
                "deals_created": r_deals_created,
                "deals_won": r_deals_won,
            })

        # Sort: unassigned always last
        by_rep.sort(key=lambda r: (r["email"] == "unassigned", -r["emails_sent"]))

        # Inject targets into rep entries
        for rep_entry in by_rep:
            inject_targets(rep_entry, target_list)

        team = {
            "emails_sent": emails_sent,
            "emails_sent_prior": emails_sent_prior,
            "emails_tracked": emails_tracked,
            "tracking_coverage": round(tracking_coverage, 2),
            "open_rate": round(open_rate, 2) if open_rate is not None else None,
            "open_rate_prior": round(open_rate_prior, 2) if open_rate_prior is not None else None,
            "reply_rate": round(reply_rate, 3),
            "reply_rate_prior": round(reply_rate_prior, 3),
            "replies": replies,
            "replies_prior": replies_prior,
            "bounces": _sum("bounces"),
            "recorded_calls": recorded_calls,
            "recorded_calls_prior": recorded_calls_prior,
            "calls_with_transcript": calls_with_transcript,
            "transcript_coverage": round(transcript_coverage, 2),
            "avg_call_duration_min": round(avg_duration, 1),
            "meetings_booked": _sum("meetings_booked"),
            "meetings_booked_prior": _sum_prior("meetings_booked"),
            "deals_created": _sum("deals_created"),
            "deals_created_prior": _sum_prior("deals_created"),
            "deals_won": _sum("deals_won"),
            "deals_won_prior": _sum_prior("deals_won"),
            "sequences_active": len(sequence_list),
            "sequence_list": sequence_list,
        }

        return {"team": team, "by_rep": by_rep}

    slices = {}
    for key, (p_start, p_end) in periods.items():
        slices[key] = _compute_slice(key, p_start, p_end)

    # ------------------------------------------------------------------
    # by_day and by_day_by_rep (only the main window, not extended)
    # ------------------------------------------------------------------
    by_day = []
    for d in _date_range(window_start, today):
        bucket = team_daily.get(d, {})
        by_day.append({
            "date": d.isoformat(),
            "emails_sent": bucket.get("emails_sent", 0),
            "replies": bucket.get("replies", 0),
            "recorded_calls": bucket.get("recorded_calls", 0),
            "meetings": bucket.get("meetings_booked", 0),
            "deals_created": bucket.get("deals_created", 0),
        })

    by_day_by_rep = {}
    for rep, daily in rep_daily.items():
        rep_days = []
        for d in _date_range(window_start, today):
            bucket = daily.get(d, {})
            rep_days.append({
                "date": d.isoformat(),
                "emails_sent": bucket.get("emails_sent", 0),
                "replies": bucket.get("replies", 0),
                "recorded_calls": bucket.get("recorded_calls", 0),
                "meetings": bucket.get("meetings_booked", 0),
                "deals_created": bucket.get("deals_created", 0),
                "deals_won": bucket.get("deals_won", 0),
            })
        by_day_by_rep[rep] = rep_days

    # ------------------------------------------------------------------
    # by_rep_detail (drawer data)
    # ------------------------------------------------------------------
    by_rep_detail = {}
    # Build owner_email_to_id reverse map
    owner_email_to_id = {info["email"]: oid for oid, info in hs_owners.items()}

    def _call_owner_func(call):
        return resolve_call_owner(call, all_rep_emails) or "unassigned"

    for rep_email in all_rep_emails:
        if rep_email == "unassigned":
            continue
        oid = owner_email_to_id.get(rep_email, "")
        by_rep_detail[rep_email] = build_rep_detail(
            rep_email=rep_email,
            owner_id=oid,
            all_deals=hs_deals,
            all_calls=ff_calls,
            all_meetings=hs_meetings,
            thresholds=stage_thresholds,
            today=today,
            stage_labels=hs_stages,
            pipeline_labels=pipeline_labels,
            won_lost_ids=won_lost_stage_ids,
            owner_id_to_email=owner_id_to_email,
            call_owner_func=_call_owner_func,
        )

    # SEQUENCES: no join key found in Mixmax livefeed payload.
    # Livefeed messages do not include a sequence/sequenceId field.
    # Sequence attribution deferred to v3 pending /sequences/{id}/sent investigation.

    # ------------------------------------------------------------------
    # Assemble output
    # ------------------------------------------------------------------
    output = {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_at_et": now_et.isoformat(),
        "window": {
            "start": window_start.isoformat(),
            "end": today.isoformat(),
            "days": days,
        },
        "timezone": "America/New_York",
        "sources": [s for s in ["mixmax", "fireflies", "hubspot"]
                     if (s == "mixmax" and mixmax_tokens) or
                        (s == "fireflies" and fireflies_key) or
                        (s == "hubspot" and hubspot_token)],
        "honesty": honesty,
        "open_pipeline": open_pipeline,
        "slices": slices,
        "by_day": by_day,
        "by_day_by_rep": by_day_by_rep,
        "by_rep_detail": by_rep_detail,
        "pipeline_stages": hs_stages,
        "stage_thresholds": stage_thresholds,
    }

    return output


def main():
    parser = argparse.ArgumentParser(description="Fetch performance data")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days")
    parser.add_argument("--out", default="test_output/performance.json", help="Output path")
    args = parser.parse_args()

    data = build_performance_data(days=args.days)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    # Atomic write
    tmp_path = args.out + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp_path, args.out)

    logger.info("Wrote %s (%.1f KB)", args.out, os.path.getsize(args.out) / 1024)
    logger.info("Sources: %s", ", ".join(data["sources"]))
    s = data["slices"].get("30d", {}).get("team", {})
    logger.info("30d summary: %d emails, %d calls, %d meetings, %d deals created",
                s.get("emails_sent", 0), s.get("recorded_calls", 0),
                s.get("meetings_booked", 0), s.get("deals_created", 0))


if __name__ == "__main__":
    main()
