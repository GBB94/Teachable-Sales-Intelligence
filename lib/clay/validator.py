"""
Payload validation for Clay.com v3 webhook submissions.

Validates seed, exclude, and re-engagement payloads before sending.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ValidationResult:
    valid: bool
    errors: list = field(default_factory=list)


def _check_non_empty_string(payload, field_name, errors):
    """Check that a field is a non-empty string."""
    val = payload.get(field_name)
    if not val or not isinstance(val, str) or not val.strip():
        errors.append(f"'{field_name}' is required and must be a non-empty string")
        return False
    return True


def _check_normalized_domain(domain, errors, required=False):
    """Validate a normalized domain: lowercase, no www., no whitespace."""
    if not domain:
        if required:
            errors.append("'normalized_domain' is required")
        return not required
    if not isinstance(domain, str):
        errors.append("'normalized_domain' must be a string")
        return False
    if domain != domain.lower():
        errors.append(f"'normalized_domain' must be lowercase, got '{domain}'")
        return False
    if domain.startswith("www."):
        errors.append(f"'normalized_domain' must not start with 'www.', got '{domain}'")
        return False
    if " " in domain or "\t" in domain:
        errors.append(f"'normalized_domain' must not contain whitespace, got '{domain}'")
        return False
    return True


def _check_iso_timestamp(payload, field_name, errors):
    """Check that a field is a valid ISO timestamp."""
    val = payload.get(field_name)
    if not val:
        return True  # Optional
    try:
        datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        errors.append(f"'{field_name}' is not a valid ISO timestamp: '{val}'")
        return False


def _check_semicolons_not_commas(payload, field_name, errors):
    """Check that CSV fields use semicolons, not commas as delimiters."""
    val = payload.get(field_name, "")
    if not val:
        return True
    # If there are commas but no semicolons, it's probably using wrong delimiter
    if "," in val and ";" not in val and val.count(",") > 0:
        errors.append(f"'{field_name}' must use semicolons as delimiters, not commas. Got: '{val[:60]}'")
        return False
    return True


def validate_seed_payload(payload):
    """Validate a seed company payload for Clay webhook."""
    errors = []

    _check_non_empty_string(payload, "company_name", errors)
    _check_non_empty_string(payload, "company_id", errors)
    _check_non_empty_string(payload, "seed_set_id", errors)
    _check_non_empty_string(payload, "idempotency_key", errors)

    # normalized_domain allowed to be empty if company_id is present
    _check_normalized_domain(payload.get("normalized_domain", ""), errors, required=False)

    # priority_score: number 0-100
    score = payload.get("priority_score")
    if score is None:
        errors.append("'priority_score' is required")
    elif not isinstance(score, (int, float)) or score < 0 or score > 100:
        errors.append(f"'priority_score' must be a number 0-100, got {score}")

    # CSV fields must use semicolons
    _check_semicolons_not_commas(payload, "features_requested_csv", errors)
    _check_semicolons_not_commas(payload, "competitors_csv", errors)

    _check_iso_timestamp(payload, "exported_at", errors)

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_exclude_payload(payload):
    """Validate an exclude list payload for Clay webhook."""
    errors = []

    # Must have normalized_domain OR linkedin_url
    domain = payload.get("normalized_domain", "")
    linkedin = payload.get("linkedin_url", "")
    if not domain and not linkedin:
        errors.append("Must have 'normalized_domain' or 'linkedin_url' (at least one)")

    if domain:
        _check_normalized_domain(domain, errors)

    _check_non_empty_string(payload, "idempotency_key", errors)

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_re_engagement_payload(payload):
    """Validate a re-engagement alert payload for Clay webhook."""
    errors = []

    _check_non_empty_string(payload, "company_id", errors)

    # score_delta: required, positive number
    delta = payload.get("score_delta")
    if delta is None:
        errors.append("'score_delta' is required")
    elif not isinstance(delta, (int, float)) or delta <= 0:
        errors.append(f"'score_delta' must be a positive number, got {delta}")

    # idempotency_key format: {company_id}_{yyyy-ww}
    key = payload.get("idempotency_key", "")
    if key:
        if not re.match(r".+_\d{4}-W\d{2}$", key):
            errors.append(f"'idempotency_key' should be '{{company_id}}_{{yyyy-Www}}' format, got '{key}'")

    _check_iso_timestamp(payload, "exported_at", errors)

    return ValidationResult(valid=len(errors) == 0, errors=errors)
