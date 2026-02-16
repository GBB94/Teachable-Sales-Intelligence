"""
Intelligence-to-variables mapper for Mixmax sequences.

Takes a Clay-enriched contact + seed intelligence from the dashboard
and produces the variable dict Mixmax needs. Handles the hypothesis
vs. fact distinction so sequence copy doesn't depend on SDR phrasing.

Hypothesis (from seed intelligence): hedged language — "typically",
"organizations like yours", "many companies in your space".

Fact (from Clay tech stack enrichment): direct language — "I see
you're using X."
"""

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pain point lookup: (segment_keyword, feature) -> pain point description
# ---------------------------------------------------------------------------
PAIN_POINTS = {
    # Continuing Education & Credentialing
    ("credentialing", "Certificate Automation"): "manual certificate issuance slowing down credential delivery",
    ("credentialing", "Compliance Reporting"): "difficulty tracking and reporting CE credit completion",
    ("credentialing", "Video Scrub Prevention"): "learners skipping required video content without proper completion enforcement",
    ("credentialing", "Organization Hierarchy / Nesting"): "managing multiple departments or locations under one training umbrella",
    ("credentialing", "Completion Tracking"): "lack of visibility into learner progress across courses and cohorts",

    # Professional Training & Development
    ("training", "SCORM Compliance"): "training content that doesn't port across LMS platforms",
    ("training", "Organization-level Reporting"): "fragmented reporting across teams and departments",
    ("training", "Multi-language Support"): "inability to deliver training in multiple languages at scale",
    ("training", "Embedded Course Widget"): "needing to embed training directly in existing portals or intranets",
    ("training", "AI Tutor / Copilot Integration"): "scaling personalized learner support without adding headcount",

    # Coaches & Consultants
    ("coaches", "White Label Platform"): "their brand getting lost behind a generic platform experience",
    ("coaches", "Student-Counselor Assignment"): "manually matching learners with the right coach or counselor",
    ("coaches", "Hybrid Scheduling"): "coordinating live sessions alongside self-paced content",

    # Course Creators & Digital Educators
    ("creators", "Landing Pages / Site Builder"): "relying on external tools to build sales pages for courses",
    ("creators", "Quiz / Assessment Reporting"): "limited insight into how learners perform on assessments",
    ("creators", "Course Module Structure"): "difficulty organizing complex curricula into logical learning paths",
}

# Segment keyword mapping for pain point lookups
_SEGMENT_KEYWORDS = {
    "Continuing Education & Credentialing": "credentialing",
    "CE & Credentialing": "credentialing",
    "Professional Training & Development": "training",
    "Professional Training": "training",
    "Coaches & Consultants": "coaches",
    "Course Creators & Digital Educators": "creators",
    "Government & Public Sector Education": "training",
}


# ---------------------------------------------------------------------------
# Social proof by segment
# ---------------------------------------------------------------------------
SOCIAL_PROOF = {
    "Continuing Education & Credentialing": "CE providers processing thousands of credentials annually",
    "CE & Credentialing": "CE providers processing thousands of credentials annually",
    "Professional Training & Development": "training organizations scaling instructor-led programs online",
    "Professional Training": "training organizations scaling instructor-led programs online",
    "Coaches & Consultants": "coaching practices managing hundreds of active client relationships",
    "Course Creators & Digital Educators": "creators building six-figure course businesses on Teachable",
    "Government & Public Sector Education": "public sector organizations standardizing training delivery",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _segment_key(segment):
    """Map full segment name to a short keyword for pain point lookups."""
    return _SEGMENT_KEYWORDS.get(segment, "")


def _derive_pain_point(features, segment):
    """Return the best pain point string for the given features + segment."""
    seg_key = _segment_key(segment)
    if not seg_key:
        return ""
    for feat in features:
        pain = PAIN_POINTS.get((seg_key, feat))
        if pain:
            return pain
    return ""


def _derive_pain_point_sentence(features, segment):
    """Build a complete hedged sentence from the pain point."""
    pain = _derive_pain_point(features, segment)
    if not pain:
        return ""
    return f"Organizations in {segment} often struggle with {pain}."


def _derive_social_proof(segment):
    """Return social proof string for the segment."""
    return SOCIAL_PROOF.get(segment, "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def map_contact_to_variables(contact, seed_intelligence, enrichment_facts=None):
    """
    Map a Clay-enriched contact + dashboard intelligence to Mixmax variables.

    Parameters
    ----------
    contact : dict
        Enriched contact from Clay:
        {email, first_name, last_name, title, company, domain}

    seed_intelligence : dict
        From the dashboard snapshot (the seed that generated this lookalike):
        {segment, features_requested, competitors_mentioned, score}

    enrichment_facts : dict, optional
        From Clay tech stack detection:
        {detected_lms, industry, employee_count}

    Returns
    -------
    dict
        Mixmax variables ready for sequence enrollment.
    """
    features = seed_intelligence.get("features_requested", [])
    competitors = seed_intelligence.get("competitors_mentioned", [])
    segment = seed_intelligence.get("segment", "")
    facts = enrichment_facts or {}

    variables = {
        # Facts (from Clay enrichment — safe to state directly)
        "first_name": contact.get("first_name", ""),
        "last_name": contact.get("last_name", ""),
        "company": contact.get("company", ""),
        "title": contact.get("title", ""),

        # Hypotheses (from seed intelligence — use hedged language in copy)
        "segment": segment,
        "feature_1": features[0] if len(features) > 0 else "",
        "feature_2": features[1] if len(features) > 1 else "",
        "feature_3": features[2] if len(features) > 2 else "",
        "pain_point": _derive_pain_point(features, segment),
        "pain_point_sentence": _derive_pain_point_sentence(features, segment),
        "social_proof": _derive_social_proof(segment),
    }

    # Competitor handling: fact vs. hypothesis
    detected_lms = facts.get("detected_lms", "")
    if detected_lms:
        # Fact: Clay actually detected their tech stack
        variables["competitor"] = detected_lms
        variables["competitor_is_fact"] = "true"
        variables["competitor_sentence"] = f"I see you're currently using {detected_lms}."
    elif competitors:
        # Hypothesis: seed companies mentioned this competitor
        # Extract name if competitor is a dict
        comp = competitors[0]
        if isinstance(comp, dict):
            comp = comp.get("competitor", comp.get("name", ""))
        variables["competitor"] = comp
        variables["competitor_is_fact"] = "false"
        variables["competitor_sentence"] = (
            f"Many {segment} organizations are evaluating alternatives to {comp}."
            if comp else ""
        )
    else:
        variables["competitor"] = ""
        variables["competitor_is_fact"] = "false"
        variables["competitor_sentence"] = ""

    return variables
