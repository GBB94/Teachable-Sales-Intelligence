"""
HTTP client for Mixmax REST API.

Authentication via X-API-Token header (from MIXMAX_API_TOKEN env var).
Rate limit: 120 requests per 60 seconds per user/IP.
"""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mixmax.com/v1"


class MixmaxClient:
    def __init__(self):
        self.api_token = os.environ.get("MIXMAX_API_TOKEN", "")
        if not self.api_token:
            logger.warning("MIXMAX_API_TOKEN not set")

    def _headers(self):
        return {
            "X-API-Token": self.api_token,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Sequences
    # ------------------------------------------------------------------
    def list_sequences(self):
        """GET /sequences — list available sequences."""
        resp = requests.get(f"{BASE_URL}/sequences", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_sequence(self, sequence_id):
        """GET /sequences/:id — get sequence details."""
        resp = requests.get(
            f"{BASE_URL}/sequences/{sequence_id}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Recipients
    # ------------------------------------------------------------------
    def add_recipient(self, sequence_id, email, variables=None):
        """POST /sequences/:id/recipients — add one recipient."""
        payload = {"email": email}
        if variables:
            payload["variables"] = variables
        resp = requests.post(
            f"{BASE_URL}/sequences/{sequence_id}/recipients",
            headers=self._headers(),
            json=[payload],  # API expects array
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def add_recipients_batch(self, sequence_id, recipients):
        """
        Add multiple recipients to a sequence.

        recipients: list of {email, variables} dicts.
        """
        resp = requests.post(
            f"{BASE_URL}/sequences/{sequence_id}/recipients",
            headers=self._headers(),
            json=recipients,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def get_sequence_recipients(self, sequence_id, limit=50, offset=0):
        """GET /sequences/:id/recipients — check who's already enrolled."""
        resp = requests.get(
            f"{BASE_URL}/sequences/{sequence_id}/recipients",
            headers=self._headers(),
            params={"limit": limit, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------
    def test_connectivity(self):
        """Quick check: can we reach the API with our token?"""
        try:
            resp = requests.get(
                f"{BASE_URL}/sequences",
                headers=self._headers(),
                params={"limit": 1},
                timeout=10,
            )
            return {
                "reachable": resp.status_code == 200,
                "status_code": resp.status_code,
            }
        except Exception as e:
            return {"reachable": False, "error": str(e)}
