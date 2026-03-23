#!/usr/bin/env python3
"""
Patch Cleanup Script — Data cleanup for features.json
Steps: prune mentions, add consolidation mappings, fix competitors, fix company names
Then re-run consolidation + normalize-companies via CLI.
"""

import json
import os
import sys

FEATURES_PATH = "test_output/features.json"
CONSOL_MAP_PATH = "feature_consolidation_map.json"
ALIASES_PATH = "config/company_aliases.json"

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Wrote {path}")

# ─────────────────────────────────────────────
# Step 2: Remove 20 low-quality mentions
# ─────────────────────────────────────────────
def step2_prune_mentions(data):
    print("\n=== STEP 2: Prune 20 low-quality mentions ===")

    ids_to_remove = {
        # Low-confidence ≤ 0.4
        "8da932562e7f",  # Organization Hierarchy / Nesting - GoLabs
        "6d8bf1cb818b",  # Completion Tracking - GoLabs
        "19176dfe0b4f",  # Community Features - Micro School Institute
        # Low-confidence 0.6
        "94f219e0c561",  # Organization Admin Role - NICUity
        "e05df309a726",  # Organization-level Reporting - NICUity
        "63d6a769fdc4",  # White Glove Onboarding - Speravita
        "6be2a9f07fba",  # Headless / API Access - WIAA Officials
        "d06982249538",  # Blog / External Content Link - Welugu Foundation
        "bb292669202c",  # Integrated One-on-One Coaching Sessions - Micro School Institute
        "e29897f0d382",  # Membership / Subscription Pricing - Micro School Institute
        "f5d28a83a97a",  # White Label Mobile App - Preply
        "7f78f0a37755",  # Zapier Integration - ACOA
        "c2fbce050139",  # Organization Hierarchy / Nesting - The Exeter Club
        "9750421649e1",  # Organization Hierarchy / Nesting - Vyagale Academy
        "2e3ee8ddce60",  # Volume Discounts Over Time - Vyagale Academy
        "a983b4a3a824",  # Health / Nutrition Course Content - Johnson Health Center
        "ea4beff42cbe",  # Renewal Notifications - Johnson Health Center
        # Non-feature drops
        "9c8daa0a9e4e",  # Sponsored Ad Read Partnership - Steve Kinney Audio
        "2aa83108d479",  # Content Ready by Launch Date - Speravita
        "aeee39497f37",  # Flexible Session Duration (15/30 min) - Vyagale Academy
    }

    before = len(data["mentions"])
    data["mentions"] = [m for m in data["mentions"] if m["mention_id"] not in ids_to_remove]
    removed = before - len(data["mentions"])

    # Verify we got them all
    remaining_ids = ids_to_remove - {m["mention_id"] for m in data["mentions"]}
    if remaining_ids != ids_to_remove:
        found_and_removed = ids_to_remove - remaining_ids
        not_found = ids_to_remove - found_and_removed - {m["mention_id"] for m in data["mentions"]}

    print(f"  Removed {removed} mentions (expected 20)")
    if removed != 20:
        missing = ids_to_remove - {m["mention_id"] for m in data["mentions"]}
        still_present = ids_to_remove & {m["mention_id"] for m in data["mentions"]}
        if still_present:
            print(f"  WARNING: {len(still_present)} IDs still present: {still_present}")
        not_found_count = 20 - removed - len(still_present)
        if not_found_count > 0:
            print(f"  WARNING: {not_found_count} IDs were not found in mentions")

    # Update stats
    unique_features = len({m["keyword"] for m in data["mentions"]})
    data["stats"]["total_mentions"] = len(data["mentions"])
    data["stats"]["unique_features"] = unique_features
    print(f"  New total: {data['stats']['total_mentions']} mentions, {unique_features} unique features")

    return data


# ─────────────────────────────────────────────
# Step 1: Fix NEEDS_REVIEW competitors
# ─────────────────────────────────────────────
NEW_COMPETITORS = [
    {
        "id": "digiforma",
        "name": "Digiforma",
        "type": "lms",
        "short_description": "French LMS platform for vocational training organizations.",
        "description": "French LMS for vocational training. Mentioned by Axio-Formation as platform they're switching from.",
        "differentiator": "French market. Vocational training focus. Auto file upload feedback."
    },
    {
        "id": "knowledge-city",
        "name": "Knowledge City",
        "type": "lms",
        "short_description": "Corporate training library with 10,000+ standardized courses.",
        "description": "Corporate training content library with pre-built courses for Microsoft Suite, leadership, etc. Used alongside Teachable by City of Albuquerque.",
        "differentiator": "Pre-built course library (10K+ courses). Complementary to custom course platforms."
    },
    {
        "id": "study-fetch",
        "name": "Study Fetch",
        "type": "adjacent",
        "short_description": "AI learning platform that generates quizzes and learning games from uploaded content.",
        "description": "Student-driven AI learning platform. Evaluated by Choices & Community Living but rejected for cost and HIPAA concerns.",
        "differentiator": "AI-generated quizzes from content. Student-facing. HIPAA concerns for healthcare use."
    },
    {
        "id": "edapp",
        "name": "EdApp (Safety Culture)",
        "type": "lms",
        "short_description": "Mobile-first microlearning LMS being sunset after Safety Culture acquisition.",
        "description": "Mobile microlearning LMS discontinued March 2026 after Safety Culture acquisition. Creates migration opportunities.",
        "differentiator": "Mobile-first microlearning. Being sunset — users forced to migrate."
    },
    {
        "id": "factorial",
        "name": "Factorial",
        "type": "adjacent",
        "short_description": "HR/talent management platform with employee onboarding and lifecycle tracking.",
        "description": "HR platform for onboarding and employee lifecycle. Not an LMS — adjacent tool for different use case.",
        "differentiator": "HR/talent management. Handles onboarding, benefits, time tracking — not courses."
    }
]

# Mapping: company -> correct competitor name
COMPETITOR_FIXES = {
    "Axio-Formation": "Digiforma",
    "City of Albuquerque": "Knowledge City",
    "Choices & Community Living": "Study Fetch",
    "GoLabs": "Factorial",
}

# These need special matching since company name might not be in the call data easily
COMPETITOR_FIXES_BY_CALL_TITLE = {
    # Multiple patterns for the Fellowship Org call (title varies)
    "Meeting with Teachable (2026-03-19)": "EdApp (Safety Culture)",
}

# Also match by company name substring
COMPETITOR_FIXES_BY_COMPANY_SUBSTR = {
    "Fellowship": "EdApp (Safety Culture)",
}

# Already in catalog, just fix the reference
ALREADY_IN_CATALOG = {
    "Lines for Life": "Articulate 360",
    "Success Gyan": "TagMango",
    "IAS Online": "WordPress + LMS Plugin",
}

# Drop entirely
DROP_COMPETITOR_COMPANIES = {"Jozeph Danial"}


def step1_fix_competitors(data):
    print("\n=== STEP 1: Fix NEEDS_REVIEW competitors ===")

    # Add new competitors to catalog and competitors arrays
    existing_ids = {c["id"] for c in data.get("competitors", [])}
    catalog_ids = {c["id"] for c in data.get("competitor_catalog", [])}

    added = 0
    for comp in NEW_COMPETITORS:
        if comp["id"] not in existing_ids:
            data["competitors"].append(comp)
            added += 1
        if comp["id"] not in catalog_ids:
            data["competitor_catalog"].append(comp)

    print(f"  Added {added} new competitors to catalog")

    # Build lookup: all fix mappings by company name
    all_fixes = {}
    all_fixes.update(COMPETITOR_FIXES)
    all_fixes.update(ALREADY_IN_CATALOG)

    # Context-based fallback: match competitor name from context text
    CONTEXT_KEYWORDS = {
        "Digiforma": "Digiforma",
        "Knowledge City": "Knowledge City",
        "Study Fetch": "Study Fetch",
        "Factorial": "Factorial",
        "EdApp": "EdApp (Safety Culture)",
        "Articulate 360": "Articulate 360",
        "TagMango": "TagMango",
        "WordPress": "WordPress + LMS Plugin",
    }

    # Context patterns for dropping
    DROP_CONTEXT_KEYWORDS = ["Unnamed competitor", "unnamed competitor"]

    def _resolve_competitor(cm, company, call_title):
        """Resolve a NEEDS_REVIEW competitor mention to a real name."""
        # 1. Match by company name (exact)
        if company in all_fixes:
            return all_fixes[company], "keep"
        # 2. Match by company name (substring)
        for substr, comp_name in COMPETITOR_FIXES_BY_COMPANY_SUBSTR.items():
            if substr in company:
                return comp_name, "keep"
        # 3. Match by call title
        for title_key, comp_name in COMPETITOR_FIXES_BY_CALL_TITLE.items():
            if title_key in call_title:
                return comp_name, "keep"
        # 4. Match by context keywords
        context = cm.get("context", "")
        for keyword, comp_name in CONTEXT_KEYWORDS.items():
            if keyword in context:
                return comp_name, "keep"
        # 5. Check if should be dropped
        if company in DROP_COMPETITOR_COMPANIES:
            return None, "drop"
        for kw in DROP_CONTEXT_KEYWORDS:
            if kw in cm.get("context", ""):
                return None, "drop"
        return None, "unknown"

    # Fix competitor_mentions in calls array
    fixes = 0
    drops = 0
    unfixed = 0
    for call in data.get("calls", []):
        company = (call.get("marketing_data") or {}).get("company", "")
        call_title = call.get("title", "")

        # Fix in competitor_mentions
        comp_mentions = call.get("competitor_mentions", [])
        new_comp_mentions = []
        for cm in comp_mentions:
            if cm.get("competitor") == "NEEDS_REVIEW":
                resolved, action = _resolve_competitor(cm, company, call_title)
                if action == "keep" and resolved:
                    cm["competitor"] = resolved
                    new_comp_mentions.append(cm)
                    fixes += 1
                elif action == "drop":
                    drops += 1
                else:
                    new_comp_mentions.append(cm)
                    unfixed += 1
                    print(f"  WARNING: Unresolved competitor in '{call_title}' (company: {company})")
            else:
                new_comp_mentions.append(cm)
        call["competitor_mentions"] = new_comp_mentions

        # Fix competitors_mentioned in recap data AND marketing_data
        for container_key in ["recap", "marketing_data"]:
            container = call.get(container_key)
            if not isinstance(container, dict):
                continue
            comp_mentioned = container.get("competitors_mentioned", [])
            if not isinstance(comp_mentioned, list):
                continue
            new_comp_mentioned = []
            for cm in comp_mentioned:
                name_field = cm.get("name", "")
                if name_field == "NEEDS_REVIEW":
                    resolved, action = _resolve_competitor(
                        {"context": cm.get("context", "")}, company, call_title
                    )
                    if action == "keep" and resolved:
                        cm["name"] = resolved
                        new_comp_mentioned.append(cm)
                        fixes += 1
                    elif action == "drop":
                        drops += 1
                    else:
                        new_comp_mentioned.append(cm)
                else:
                    new_comp_mentioned.append(cm)
            container["competitors_mentioned"] = new_comp_mentioned

    # Fix competitor_context in mentions array
    for mention in data.get("mentions", []):
        if mention.get("competitor_context") == "NEEDS_REVIEW":
            m_company = mention.get("company", "")
            m_call_title = mention.get("call_title", "")
            resolved, action = _resolve_competitor(
                {"context": mention.get("text", "")}, m_company, m_call_title
            )
            if action == "keep" and resolved:
                mention["competitor_context"] = resolved
                fixes += 1
            elif action == "drop":
                mention.pop("competitor_context", None)
                fixes += 1
            else:
                # Last resort: check call title for any known competitor context
                for title_key, comp_name in COMPETITOR_FIXES_BY_CALL_TITLE.items():
                    if title_key in m_call_title:
                        mention["competitor_context"] = comp_name
                        fixes += 1
                        break

    # Sweep all remaining NEEDS_REVIEW strings in derived sections
    # (company_summaries, prospecting_snapshot, etc.)
    _sweep_needs_review(data)

    print(f"  Fixed {fixes} competitor references in calls")
    print(f"  Dropped {drops} competitor mention(s) (too vague)")
    if unfixed:
        print(f"  WARNING: {unfixed} unresolved NEEDS_REVIEW competitor(s) remain")

    return data


def _sweep_needs_review(data):
    """Recursively remove/fix all remaining NEEDS_REVIEW in derived/computed sections."""
    swept = [0]  # mutable counter

    CONTEXT_KEYWORDS = {
        "Digiforma": "Digiforma", "Knowledge City": "Knowledge City",
        "Study Fetch": "Study Fetch", "Factorial": "Factorial",
        "EdApp": "EdApp (Safety Culture)", "Articulate 360": "Articulate 360",
        "TagMango": "TagMango", "WordPress": "WordPress + LMS Plugin",
    }

    # Skip these top-level keys (primary data handled elsewhere)
    PRIMARY_KEYS = {"mentions", "calls", "competitors", "competitor_catalog"}

    def _fix_list(lst, parent_key=""):
        """Remove NEEDS_REVIEW strings from lists, fix dicts in lists."""
        new_list = []
        for item in lst:
            if item == "NEEDS_REVIEW":
                swept[0] += 1
                continue
            if isinstance(item, dict):
                # Remove segment summaries or competitor analysis rollups with NEEDS_REVIEW
                if item.get("name") == "NEEDS_REVIEW" and "company_count" in item:
                    swept[0] += 1
                    continue  # drop entire segment summary
                if item.get("competitor") == "NEEDS_REVIEW" and "mention_count" in item:
                    swept[0] += 1
                    continue  # drop entire competitor analysis rollup
                # Fix competitor/name fields in detail entries
                for field in ["competitor", "name"]:
                    if item.get(field) == "NEEDS_REVIEW":
                        context = item.get("context", "")
                        resolved = None
                        for kw, cn in CONTEXT_KEYWORDS.items():
                            if kw in context:
                                resolved = cn
                                break
                        if resolved:
                            item[field] = resolved
                            swept[0] += 1
                        elif "Unnamed" in context or "unnamed" in context:
                            swept[0] += 1
                            continue  # drop this item
                        # else keep as-is
                _fix_dict(item)
            elif isinstance(item, list):
                _fix_list(item)
            new_list.append(item)
        lst.clear()
        lst.extend(new_list)

    def _fix_dict(d):
        """Recursively fix NEEDS_REVIEW in dict values."""
        keys_to_delete = []
        for k, v in d.items():
            if isinstance(v, list):
                _fix_list(v, k)
            elif isinstance(v, dict):
                # Remove NEEDS_REVIEW segment entries (name == NEEDS_REVIEW)
                if v.get("name") == "NEEDS_REVIEW" and "company_count" in v:
                    keys_to_delete.append(k)
                    swept[0] += 1
                else:
                    _fix_dict(v)
        for k in keys_to_delete:
            del d[k]

    # Process all non-primary sections
    for key in list(data.keys()):
        if key in PRIMARY_KEYS:
            continue
        val = data[key]
        if isinstance(val, dict):
            _fix_dict(val)
        elif isinstance(val, list):
            _fix_list(val, key)

    # Also clean company_scores key
    scores = data.get("company_scores", {})
    if "NEEDS_REVIEW" in scores:
        del scores["NEEDS_REVIEW"]
        swept[0] += 1

    if swept[0]:
        print(f"  Swept {swept[0]} NEEDS_REVIEW references from derived sections")


# ─────────────────────────────────────────────
# Step 3: Add consolidation mappings
# ─────────────────────────────────────────────
NEW_CONSOLIDATION_MAPPINGS = {
    "AI Course Feedback to Students": {"canonical": "AI Tutor / Copilot Integration", "category": "Platform & Integrations"},
    "Aggregated Survey Responses": {"canonical": "Quiz / Assessment Reporting", "category": "Reporting & Analytics"},
    "Auto-Scroll Transcript": {"canonical": "Learner UX / Course Journey", "category": "Design & Branding"},
    "Event Calendar & Registration": {"canonical": "Hybrid Scheduling", "category": "Engagement & Community"},
    "Free Course Hosting (No Revenue Collection)": {"canonical": "Freemium / Free Course Support", "category": "Commerce & Checkout"},
    "Member Login Tracking": {"canonical": "Completion Tracking", "category": "Reporting & Analytics"},
    "Reflection Prompts / Open-Ended Responses": {"canonical": "Open-ended Questions / Journal", "category": "Assessments & Quizzes"},
    "Student Institution Tracking": {"canonical": "Student Custom Fields / Onboarding", "category": "User Management"},
    "Teacher-to-Parent Feedback Form": {"canonical": "Quiz / Assessment Reporting", "category": "Reporting & Analytics"},
    "SSO with Discord": {"canonical": "SSO / SAML", "category": "Platform & Integrations"},
    "VAT Configuration by Seller Country": {"canonical": "International Payment Processing", "category": "Commerce & Checkout"},
    "Video Embedding in Community": {"canonical": "Community Features", "category": "Engagement & Community"},
    "YouTube Shopping Integration": {"canonical": "Third-party Integration", "category": "Platform & Integrations"},
    "Nonprofit Discount": {"canonical": "SMB / Flexible Pricing Tiers", "category": "Commerce & Checkout"},
    "PDF Quotation / Contract Export": {"canonical": "Purchase Order / Invoice Billing", "category": "Commerce & Checkout"},
    "Competitive Payment Processing Rates": {"canonical": "International Payment Processing", "category": "Commerce & Checkout"},
}

def step3_add_consolidation_mappings():
    print("\n=== STEP 3: Add 16 consolidation mappings ===")

    consol = load_json(CONSOL_MAP_PATH)
    added = 0
    for variant, mapping in NEW_CONSOLIDATION_MAPPINGS.items():
        if variant not in consol:
            consol[variant] = mapping
            added += 1
        else:
            print(f"  Already exists: {variant}")

    save_json(CONSOL_MAP_PATH, consol)
    print(f"  Added {added} new mappings (total: {len(consol)})")


# ─────────────────────────────────────────────
# Step 4: Fix DOT Compliance case mismatch
# ─────────────────────────────────────────────
def step4_fix_dot_compliance():
    print("\n=== STEP 4: Fix DOT Compliance -> Dot Compliance ===")

    aliases = load_json(ALIASES_PATH)
    if "Dot Compliance" not in aliases.get("canonical_companies", {}):
        aliases["canonical_companies"]["Dot Compliance"] = ["DOT Compliance", "dot compliance", "Dot compliance"]
        save_json(ALIASES_PATH, aliases)
        print("  Added Dot Compliance alias")
    else:
        existing = aliases["canonical_companies"]["Dot Compliance"]
        if "DOT Compliance" not in existing:
            existing.append("DOT Compliance")
            save_json(ALIASES_PATH, aliases)
            print("  Updated Dot Compliance alias")
        else:
            print("  Alias already exists")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    print("Loading features.json...")
    data = load_json(FEATURES_PATH)
    print(f"  {data['stats']['total_mentions']} mentions, {data['stats']['unique_features']} features")

    # Execute in specified order
    data = step2_prune_mentions(data)      # Step 2 first
    step3_add_consolidation_mappings()      # Step 3 (map file only)
    data = step1_fix_competitors(data)      # Step 1
    step4_fix_dot_compliance()              # Step 4

    # Save the modified features.json
    print("\n=== Saving features.json ===")
    save_json(FEATURES_PATH, data)

    print("\n=== Done. Now run: ===")
    print("  python3 analyze_features.py consolidate test_output/index.html --map feature_consolidation_map.json")
    print("  python3 analyze_features.py normalize-companies test_output/index.html")


if __name__ == "__main__":
    main()
