"""
Email campaign generator: intelligence-driven outreach from call data.

Takes seed profiles from the ICP snapshot (features, competitors, segments,
pain points) and generates template-based email sequences grounded in
real call intelligence. Every claim links back to a specific call and quote.

Public API:
    generate_emails(seed_ids, dashboard_data, snapshot, **opts) -> dict
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outreach safety guardrails
# ---------------------------------------------------------------------------
BANNED_PHRASES = [
    "I noticed your team",
    "I noticed you",
    "I see you're using",
    "I see that you",
    "I saw your",
    "I saw that",
    "I know you",
    "I know your team",
    "your team is struggling with",
    "you're currently using",
    "you recently",
    "based on your",
    "looking at your",
    "I checked out your",
    "after reviewing your",
]

MAX_WORDS = 150


def validate_email(body, segment="", features=None):
    """
    Check generated email against safety guardrails.

    Returns: {"valid": bool, "warnings": [str]}
    """
    warnings = []

    body_lower = body.lower()
    for phrase in BANNED_PHRASES:
        if phrase.lower() in body_lower:
            warnings.append(f"Contains banned phrase: '{phrase}'")

    word_count = len(body.split())
    if word_count > MAX_WORDS:
        warnings.append(f"Email is {word_count} words (max {MAX_WORDS})")

    return {
        "valid": len(warnings) == 0,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Pain points and social proof (shared with mapper.py but email-specific)
# ---------------------------------------------------------------------------
PAIN_POINT_SENTENCES = {
    ("credentialing", "Certificate Automation"):
        "manual certificate issuance slowing down credential delivery, especially when compliance reporting is also a factor",
    ("credentialing", "Compliance Reporting"):
        "difficulty tracking and reporting CE credit completion across distributed teams",
    ("credentialing", "Video Scrub Prevention"):
        "ensuring learners actually watch required video content without skipping ahead",
    ("credentialing", "Organization Hierarchy / Nesting"):
        "managing multiple departments or locations under one training umbrella",
    ("credentialing", "Completion Tracking"):
        "lack of visibility into learner progress across courses and cohorts",
    ("training", "SCORM Compliance"):
        "training content that doesn't port cleanly across LMS platforms",
    ("training", "Organization-level Reporting"):
        "fragmented reporting across teams and departments",
    ("training", "Multi-language Support"):
        "delivering training content in multiple languages at scale",
    ("training", "Embedded Course Widget"):
        "embedding training directly into existing portals or intranets without a separate login",
    ("training", "AI Tutor / Copilot Integration"):
        "scaling personalized learner support without adding headcount",
    ("coaches", "White Label Platform"):
        "their brand getting lost behind a generic platform experience",
    ("coaches", "Student-Counselor Assignment"):
        "manually matching learners with the right coach or counselor",
    ("coaches", "Hybrid Scheduling"):
        "coordinating live sessions alongside self-paced content",
    ("creators", "Landing Pages / Site Builder"):
        "relying on external tools to build sales pages for courses",
    ("creators", "Quiz / Assessment Reporting"):
        "limited insight into how learners perform on assessments",
}

_SEGMENT_KEYWORDS = {
    "Continuing Education & Credentialing": "credentialing",
    "CE & Credentialing": "credentialing",
    "Professional Training & Development": "training",
    "Professional Training": "training",
    "Coaches & Consultants": "coaches",
    "Course Creators & Digital Educators": "creators",
    "Government & Public Sector Education": "training",
}

CAPABILITY_DESCRIPTIONS = {
    "Certificate Automation": "Certificates auto-generate on course completion, with custom branding and compliance data built in.",
    "Compliance Reporting": "Compliance data exports are built in — completion rates, credit hours, and audit trails.",
    "Video Scrub Prevention": "Video scrub prevention ensures learners watch required content in full before progressing.",
    "Organization Hierarchy / Nesting": "Multi-level org hierarchies let you manage departments, locations, and teams under one account.",
    "SCORM Compliance": "SCORM packages import natively, so existing content works without re-authoring.",
    "Quiz / Assessment Reporting": "Quiz analytics give you question-level performance data across cohorts.",
    "Completion Tracking": "Real-time completion tracking across all courses with exportable progress reports.",
    "Multi-language Support": "Multi-language support lets you deliver the same course in multiple languages from one dashboard.",
    "Embedded Course Widget": "Embed courses directly into your existing site or portal with a single code snippet.",
    "White Label Platform": "Full white-label — your domain, your branding, your login page. No Teachable branding visible.",
    "Learner File Upload to Coaches": "Learners can upload files directly to coaches for review and feedback within the platform.",
    "Drip / Timed Content Release": "Drip scheduling releases content on a timeline you control — daily, weekly, or milestone-based.",
    "Certificate Automation": "Certificates auto-generate on completion with your branding and verification links.",
    "Landing Pages / Site Builder": "Built-in page builder for sales pages, so you don't need a separate website tool.",
    "Hybrid Scheduling": "Blend self-paced content with live scheduled sessions in a single course.",
    "Community Features": "Built-in community spaces where learners can discuss, share, and connect.",
    "Third-party Integration": "Native integrations with the tools you already use — Zapier, webhooks, and direct API access.",
    "Payment Plans / Installments": "Flexible payment plans and installments built into checkout.",
    "Headless / API Access": "Full API access for headless implementations and custom integrations.",
    "AI Tutor / Copilot Integration": "AI-powered tutoring that gives learners personalized support at scale.",
}


# ---------------------------------------------------------------------------
# Intelligence brief assembler
# ---------------------------------------------------------------------------
def _build_intelligence_brief(seed, snapshot, dashboard_data):
    """
    Assemble everything we know about this profile from call data.
    Returns the brief AND structured evidence linking claims to calls.
    """
    segment = seed.get("segment", "")
    features = seed.get("features_requested", [])
    competitors = seed.get("competitors_mentioned", [])

    # Build call lookup
    calls_by_id = {}
    for call in dashboard_data.get("calls", []):
        calls_by_id[call.get("id", "")] = call

    # Find relevant calls by company name
    company_name = seed.get("company_name", "")
    relevant_call_ids = set()
    for mention in dashboard_data.get("mentions", []):
        if mention.get("company", "").lower() == company_name.lower():
            relevant_call_ids.add(mention.get("call_id", ""))

    relevant_calls = [calls_by_id[cid] for cid in relevant_call_ids if cid in calls_by_id]

    # Get feature quotes with provenance
    mentions = dashboard_data.get("mentions", [])
    feature_evidence = []
    feature_quotes = {}
    for f in features[:5]:
        matching = [
            m for m in mentions
            if m.get("keyword") == f
            and m.get("text")
            and m.get("company", "").lower() == company_name.lower()
        ]
        if matching:
            best = matching[0]
            feature_quotes[f] = best.get("text", "")[:200]
            feature_evidence.append({
                "claim": f,
                "source_call_id": best.get("call_id", ""),
                "source_company": best.get("company", company_name),
                "source_quote": best.get("text", "")[:200],
                "feature": f,
                "confidence": best.get("confidence", "medium"),
            })

    # Get company summary
    summaries = dashboard_data.get("company_summaries", {})
    company_summary = summaries.get(company_name, "")

    # Get competitor context with provenance
    competitor_context = []
    competitor_evidence = []
    for call in relevant_calls:
        for cm in call.get("competitor_mentions", []):
            comp_name = cm.get("competitor", "")
            if isinstance(cm, dict) and comp_name:
                competitor_context.append({
                    "competitor": comp_name,
                    "context": cm.get("context", "")[:200],
                    "mention_type": cm.get("mention_type", ""),
                })
                competitor_evidence.append({
                    "claim": f"competitor: {comp_name}",
                    "source_call_id": call.get("id", ""),
                    "source_company": company_name,
                    "source_quote": cm.get("context", "")[:200],
                    "feature": "",
                    "confidence": "high" if cm.get("mention_type") in ("currently_using", "switching_from") else "medium",
                })
        # Also check marketing_data.competitors_mentioned
        mkt = call.get("marketing_data", {})
        if isinstance(mkt, dict):
            for cm in mkt.get("competitors_mentioned", []):
                comp_name = cm.get("name", "") if isinstance(cm, dict) else str(cm)
                if comp_name and not any(c["competitor"] == comp_name for c in competitor_context):
                    competitor_context.append({
                        "competitor": comp_name,
                        "context": cm.get("context", "")[:200] if isinstance(cm, dict) else "",
                        "mention_type": "",
                    })

    # Derive pain point
    seg_key = _SEGMENT_KEYWORDS.get(segment, "")
    pain_point = ""
    for f in features[:3]:
        p = PAIN_POINT_SENTENCES.get((seg_key, f))
        if p:
            pain_point = p
            break

    return {
        "segment": segment,
        "features": features,
        "feature_quotes": feature_quotes,
        "competitors": [c.get("competitor", c.get("name", "")) if isinstance(c, dict) else str(c) for c in competitors],
        "competitor_context": competitor_context,
        "company_summary": company_summary,
        "company_name": company_name,
        "call_count": seed.get("call_count", 0),
        "score": seed.get("score", 0),
        "pain_point": pain_point,
        "evidence": feature_evidence + competitor_evidence,
    }


# ---------------------------------------------------------------------------
# Template-based email generator
# ---------------------------------------------------------------------------
def _generate_stage_1(brief, sender_name, sender_title):
    """Initial outreach: demonstrate you understand their world."""
    segment = brief["segment"]
    features = brief["features"]
    pain_point = brief["pain_point"]
    f1 = features[0] if features else "learning platform capabilities"
    f2 = features[1] if len(features) > 1 else ""

    # Build the capability line
    cap1 = CAPABILITY_DESCRIPTIONS.get(f1, f"Teachable handles {f1.lower()} natively.")
    cap_line = cap1

    # Build the pain point line
    if pain_point:
        pain_line = f"A common challenge we hear is {pain_point}."
    else:
        pain_line = f"Teams in {segment} often need {f1.lower()}" + (f" and {f2.lower()}" if f2 else "") + " from their learning platform."

    subject = f"{f1} for {segment} organizations"

    body = (
        f"Hi {{{{first_name}}}},\n\n"
        f"I work with {segment} organizations that are scaling their programs online.\n\n"
        f"{pain_line}\n\n"
        f"{cap_line}\n\n"
        f"Would it make sense to show you? Happy to do 15 minutes.\n\n"
        f"{sender_name}"
    )

    # Link evidence
    evidence = []
    for e in brief["evidence"]:
        if e.get("feature") == f1:
            evidence.append(e)
            break

    return {
        "stage": 1,
        "subject": subject,
        "body": body,
        "send_day": 0,
        "notes": f"Opens with segment awareness. Pain point from {brief['company_name']} call data. Capability line maps directly to their top feature request.",
        "evidence": evidence,
    }


def _generate_stage_2(brief, sender_name, sender_title):
    """Value add: provide something useful, reference a specific capability."""
    segment = brief["segment"]
    features = brief["features"]
    competitors = brief["competitors"]
    f1 = features[0] if features else "your key requirements"
    f2 = features[1] if len(features) > 1 else ""

    # Build capability detail — ideally something beyond marketing site
    detail_feature = f2 if f2 else f1
    cap_detail = CAPABILITY_DESCRIPTIONS.get(detail_feature, "")
    if not cap_detail:
        cap_detail = f"Teachable's {detail_feature.lower()} capabilities are built for teams at your scale."

    # Competitor angle if we have one
    comp_line = ""
    if competitors:
        comp = competitors[0]
        comp_line = f"\n\nMany {segment} organizations are comparing platforms right now — we've built specifically for this use case."

    subject = f"Re: {f1} for {segment} organizations"

    body = (
        f"{{{{first_name}}}},\n\n"
        f"Following up on my last note about {f1.lower()} for {segment} teams.\n\n"
        f"One thing that often surprises people: {cap_detail}"
        f"{comp_line}\n\n"
        f"Worth a quick conversation?\n\n"
        f"{sender_name}"
    )

    evidence = []
    for e in brief["evidence"]:
        if e.get("feature") == detail_feature:
            evidence.append(e)
            break

    return {
        "stage": 2,
        "subject": subject,
        "body": body,
        "send_day": 3,
        "notes": f"Value-add follow-up. Highlights {detail_feature} capability detail." + (f" Soft competitor angle ({competitors[0]})." if competitors else ""),
        "evidence": evidence,
    }


def _generate_stage_3(brief, sender_name, sender_title):
    """Soft close: short, direct, calendar link."""
    segment = brief["segment"]
    features = brief["features"]
    f1 = features[0] if features else "platform capabilities"

    subject = f"Re: {f1} for {segment} organizations"

    body = (
        f"{{{{first_name}}}},\n\n"
        f"I know things get busy — just wanted to make this easy.\n\n"
        f"If {f1.lower()} is still a priority for your team, I can show you how Teachable handles it in 15 minutes.\n\n"
        f"Here's my calendar if easier: {{{{Sender Calendar URL}}}}\n\n"
        f"{sender_name}"
    )

    return {
        "stage": 3,
        "subject": subject,
        "body": body,
        "send_day": 7,
        "notes": "Soft close. Short and direct. Calendar link for low-friction booking.",
        "evidence": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_emails(seed_ids=None, dashboard_data=None, snapshot=None,
                    sequence_length=3, sender_name="Zach",
                    sender_title="Sales, Teachable"):
    """
    Generate email campaigns for seed profiles.

    Parameters
    ----------
    seed_ids : list[str], optional
        Company IDs to generate for. If empty, uses top 3 scoring seeds.
    dashboard_data : dict
        Dashboard DATA with calls, mentions, company_summaries.
    snapshot : dict
        Current ICP snapshot with seed_companies.
    sequence_length : int
        Number of emails per sequence (1-3).
    sender_name : str
        Sender's first name for email signature.
    sender_title : str
        Sender's title for context.

    Returns
    -------
    dict with "campaigns" list.
    """
    if not snapshot:
        return {"error": "No snapshot available. Generate a snapshot first."}
    if not dashboard_data:
        return {"error": "No dashboard data available."}

    seeds = snapshot.get("seed_companies", [])
    if not seeds:
        return {"error": "No seed companies in snapshot."}

    # Filter to requested seeds, or top 3 by score
    if seed_ids:
        selected = [s for s in seeds if s.get("company_id") in seed_ids]
    else:
        selected = sorted(seeds, key=lambda s: s.get("score", 0), reverse=True)[:3]

    if not selected:
        return {"error": "No matching seed companies found."}

    campaigns = []
    for seed in selected:
        brief = _build_intelligence_brief(seed, snapshot, dashboard_data)

        # Generate emails based on sequence_length
        generators = [_generate_stage_1, _generate_stage_2, _generate_stage_3]
        emails = []
        all_evidence = list(brief["evidence"])

        for i in range(min(sequence_length, 3)):
            email = generators[i](brief, sender_name, sender_title)

            # Validate
            validation = validate_email(email["body"], brief["segment"], brief["features"])
            email["validation"] = validation

            emails.append(email)

        # Collect variables used across all emails
        variables_used = {"first_name", "company"}
        for email in emails:
            body = email["body"]
            if "{{Sender Calendar URL}}" in body:
                variables_used.add("Sender Calendar URL")
            if "{{Sender Signature}}" in body:
                variables_used.add("Sender Signature")

        campaigns.append({
            "profile": {
                "company_name": seed.get("company_name", ""),
                "company_id": seed.get("company_id", ""),
                "segment": brief["segment"],
                "score": brief["score"],
                "features": brief["features"][:5],
                "competitors": brief["competitors"][:3],
                "source_companies": [brief["company_name"]],
                "pain_point": brief["pain_point"],
            },
            "emails": emails,
            "variables_used": sorted(variables_used),
            "hypothesis_disclaimer": "These emails use inferred intelligence from similar companies. Use hedged language for lookalike outreach.",
            "all_evidence": all_evidence,
        })

    return {"campaigns": campaigns}
