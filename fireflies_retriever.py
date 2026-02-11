"""
Fireflies Call Retrieval Layer — backwards-compatible entry point.

All code has been split into:
  - models.py    (dataclasses, keyword config)
  - client.py    (FirefliesRetriever API client)
  - exports.py   (JSON, CSV, HubSpot, dashboard export)

This file re-exports the public API so existing imports keep working.
"""

from models import (
    CallFilter,
    Call,
    FeatureRequest,
    FeatureRequestReport,
    DEFAULT_FEATURE_KEYWORDS,
)
from client import FirefliesRetriever
from exports import (
    export_to_json,
    export_to_csv,
    export_hubspot_notes,
    export_feature_report,
    export_feature_report_csv,
    export_feature_dashboard,
)

__all__ = [
    "CallFilter",
    "Call",
    "FeatureRequest",
    "FeatureRequestReport",
    "FirefliesRetriever",
    "DEFAULT_FEATURE_KEYWORDS",
    "export_to_json",
    "export_to_csv",
    "export_hubspot_notes",
    "export_feature_report",
    "export_feature_report_csv",
    "export_feature_dashboard",
]
