"""
Configurable scoring engine for Clay.com prospect prioritization.

Loads scoring weights from config.json. Supports hot-reload with diff logging.
Returns 0-100 scores with per-signal breakdowns.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

_config = None


def _load_config():
    """Load config.json from disk."""
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def get_config():
    """Return current config, loading if needed."""
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def reload_config():
    """Hot-reload config.json with diff logging."""
    global _config
    old = _config
    _config = _load_config()
    if old is not None:
        _log_config_diff(old, _config)
    logger.info("Scoring config reloaded (version=%s)", _config.get("version"))
    return _config


def _log_config_diff(old, new):
    """Log differences between old and new config."""
    changes = []
    # Check signals
    old_signals = {s["name"]: s for s in old.get("signals", [])}
    new_signals = {s["name"]: s for s in new.get("signals", [])}
    for name in set(list(old_signals.keys()) + list(new_signals.keys())):
        if name not in old_signals:
            changes.append(f"  + signal '{name}' added")
        elif name not in new_signals:
            changes.append(f"  - signal '{name}' removed")
        elif old_signals[name] != new_signals[name]:
            changes.append(f"  ~ signal '{name}' modified")
    # Check boosts
    for section in ("segment_boosts", "feature_boosts"):
        if old.get(section) != new.get(section):
            changes.append(f"  ~ {section} changed")
    # Check export gates
    if old.get("export_gates") != new.get("export_gates"):
        changes.append(f"  ~ export_gates changed")
    if changes:
        logger.info("Config changes:\n%s", "\n".join(changes))
    else:
        logger.info("Config reloaded, no changes detected")


def _resolve_field(data, field_path):
    """
    Resolve dot-notation field paths from prospect data.
    e.g., 'features_requested.length' -> len(data['features_requested'])
    """
    parts = field_path.split(".")
    if len(parts) == 2 and parts[1] == "length":
        val = data.get(parts[0])
        if isinstance(val, (list, tuple)):
            return len(val)
        return 0
    # Simple field access
    return data.get(field_path, 0)


def _evaluate_condition(data, condition):
    """
    Evaluate string conditions like 'last_call_date within 14 days'.
    Returns 1 if condition is met, 0 otherwise.
    """
    match = re.match(r"(\w+)\s+within\s+(\d+)\s+days?", condition)
    if not match:
        logger.warning("Unknown condition format: %s", condition)
        return 0

    field_name = match.group(1)
    days_limit = int(match.group(2))
    date_str = data.get(field_name)

    if not date_str:
        return 0

    try:
        # Parse ISO date
        if "T" in str(date_str):
            dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(str(date_str), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = (now - dt).days
        return 1 if delta <= days_limit else 0
    except (ValueError, TypeError):
        return 0


def calculate_score(prospect_data):
    """
    Calculate a 0-100 priority score for a prospect.

    Returns:
        dict: {"total": int, "breakdown": {"signal_name": points, ...}}
    """
    config = get_config()
    breakdown = {}
    total = 0

    # Evaluate each signal
    for signal in config.get("signals", []):
        name = signal["name"]
        points_per_unit = signal.get("points_per_unit", 0)
        cap = signal.get("cap", 100)
        min_threshold = signal.get("min_threshold")

        if "condition" in signal:
            # Condition-based signal (e.g., recency)
            value = _evaluate_condition(prospect_data, signal["condition"])
        elif "field" in signal:
            # Field-based signal
            value = _resolve_field(prospect_data, signal["field"])
        else:
            continue

        # Check min threshold
        if min_threshold is not None and value < min_threshold:
            breakdown[name] = 0
            continue

        points = min(value * points_per_unit, cap)
        breakdown[name] = points
        total += points

    # Segment boosts
    segment = prospect_data.get("segment", "")
    segment_boosts = config.get("segment_boosts", {})
    if segment and segment in segment_boosts:
        boost = segment_boosts[segment]
        breakdown[f"segment_boost:{segment}"] = boost
        total += boost

    # Feature boosts (once per matching feature, not per mention)
    feature_boosts = config.get("feature_boosts", {})
    features = prospect_data.get("features_requested", [])
    if feature_boosts and features:
        for feature in features:
            if feature in feature_boosts:
                boost = feature_boosts[feature]
                breakdown[f"feature_boost:{feature}"] = boost
                total += boost

    # Clamp to 0-100
    total = max(0, min(100, total))

    return {"total": total, "breakdown": breakdown}
