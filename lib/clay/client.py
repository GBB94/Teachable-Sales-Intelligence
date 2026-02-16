"""
HTTP client for Clay.com webhook submissions (v3 — simplified).

POST with rate limiting, retries, and chunked sending for large payloads.
No webhook lifecycle, submission counting, or quarantine.
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 100_000  # 100KB Clay webhook limit


class ClayClient:
    """HTTP client for Clay webhook POSTs."""

    def __init__(self, auth_token=None, rate_limit_ms=None, retry_attempts=None, staging_mode=None):
        self.auth_token = auth_token or os.environ.get("CLAY_WEBHOOK_AUTH_TOKEN", "")
        self.rate_limit_ms = rate_limit_ms if rate_limit_ms is not None else int(os.environ.get("CLAY_RATE_LIMIT_MS", "500"))
        self.max_retries = retry_attempts if retry_attempts is not None else int(os.environ.get("CLAY_RETRY_ATTEMPTS", "3"))
        self.staging_mode = staging_mode if staging_mode is not None else (os.environ.get("CLAY_STAGING_MODE", "false").lower() == "true")
        self._last_send_time = 0

    def _resolve_url(self, url, entity_type="seed"):
        """Resolve actual webhook URL, accounting for staging mode."""
        if self.staging_mode:
            staging = os.environ.get(f"CLAY_WEBHOOK_{entity_type.upper()}S_STAGING", "")
            if staging:
                logger.info("Staging mode: routing to staging webhook for %s", entity_type)
                return staging
        return url

    def _build_headers(self):
        """Build HTTP headers."""
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _throttle(self):
        """Enforce rate limiting between requests."""
        if self.rate_limit_ms <= 0:
            return
        elapsed_ms = (time.time() - self._last_send_time) * 1000
        if elapsed_ms < self.rate_limit_ms:
            time.sleep((self.rate_limit_ms - elapsed_ms) / 1000)

    def send_to_webhook(self, url, payload):
        """
        POST a single payload to a Clay webhook URL.

        Returns: {success, status_code, response, error}
        """
        if not url:
            return {"success": False, "status_code": None, "response": None, "error": "Webhook URL not configured"}
        if not url.startswith("https://"):
            return {"success": False, "status_code": None, "response": None, "error": "Webhook URL must use HTTPS"}

        headers = self._build_headers()
        last_error = None
        last_status = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                self._last_send_time = time.time()
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
                last_status = resp.status_code

                if resp.status_code in (200, 201, 202):
                    return {"success": True, "status_code": resp.status_code, "response": resp.text, "error": None}

                if resp.status_code in (400, 401, 403):
                    return {"success": False, "status_code": resp.status_code, "response": resp.text, "error": f"{resp.status_code}: {resp.text[:200]}"}

                if resp.status_code in (429, 500, 503):
                    last_error = f"{resp.status_code}: {resp.text[:200]}"
                    if attempt < self.max_retries:
                        wait = (2 ** attempt) * 1.0
                        logger.warning("Retryable %d (attempt %d/%d), waiting %.1fs", resp.status_code, attempt + 1, self.max_retries + 1, wait)
                        time.sleep(wait)
                    continue

                return {"success": False, "status_code": resp.status_code, "response": resp.text, "error": f"{resp.status_code}: {resp.text[:200]}"}

            except requests.exceptions.Timeout:
                last_error = "Request timed out"
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                continue
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {str(e)[:200]}"
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                continue
            except requests.exceptions.RequestException as e:
                return {"success": False, "status_code": None, "response": None, "error": str(e)[:200]}

        return {"success": False, "status_code": last_status, "response": None, "error": last_error}

    def send_batch(self, url, payloads, delay_ms=None):
        """
        Send a list of payloads sequentially with rate limiting.

        Returns: {success, sent, failed, errors}
        """
        if delay_ms is not None:
            original = self.rate_limit_ms
            self.rate_limit_ms = delay_ms

        sent = 0
        failed = 0
        errors = []

        try:
            for payload in payloads:
                result = self.send_to_webhook(url, payload)
                if result["success"]:
                    sent += 1
                else:
                    failed += 1
                    errors.append({
                        "payload_id": payload.get("idempotency_key", payload.get("company_id", "")),
                        "error": result["error"],
                    })
        finally:
            if delay_ms is not None:
                self.rate_limit_ms = original

        return {
            "success": failed == 0,
            "sent": sent,
            "failed": failed,
            "errors": errors,
        }

    def send_chunked(self, url, items, chunk_size=500, delay_ms=None):
        """
        Split a large list into chunks under 100KB and send each as a batch.
        Used for exclude lists that can contain hundreds/thousands of records.

        Returns: {success, total_sent, chunks_sent, errors}
        """
        if not items:
            return {"success": True, "total_sent": 0, "chunks_sent": 0, "errors": []}

        if not url:
            return {"success": False, "total_sent": 0, "chunks_sent": 0, "errors": [{"error": "Webhook URL not configured"}]}

        chunks = []
        current_chunk = []
        current_size = 0

        for item in items:
            item_size = len(json.dumps(item).encode("utf-8"))
            # Start new chunk if adding this item would exceed limit
            if current_chunk and (current_size + item_size > MAX_PAYLOAD_BYTES - 1000 or len(current_chunk) >= chunk_size):
                chunks.append(current_chunk)
                current_chunk = []
                current_size = 0
            current_chunk.append(item)
            current_size += item_size

        if current_chunk:
            chunks.append(current_chunk)

        total_sent = 0
        chunks_sent = 0
        all_errors = []

        for chunk in chunks:
            result = self.send_batch(url, chunk, delay_ms=delay_ms)
            total_sent += result["sent"]
            if result["sent"] > 0:
                chunks_sent += 1
            all_errors.extend(result["errors"])

        return {
            "success": len(all_errors) == 0,
            "total_sent": total_sent,
            "chunks_sent": chunks_sent,
            "errors": all_errors,
        }

    def test_connectivity(self, url):
        """Test if a webhook URL is reachable."""
        if not url:
            return {"reachable": False, "status_code": None, "error": "Not configured"}
        if not url.startswith("https://"):
            return {"reachable": False, "status_code": None, "error": "Must use HTTPS"}
        try:
            resp = requests.post(url, json={}, headers=self._build_headers(), timeout=10)
            return {
                "reachable": resp.status_code < 500,
                "status_code": resp.status_code,
                "error": None if resp.status_code < 400 else resp.text[:200],
            }
        except requests.exceptions.RequestException as e:
            return {"reachable": False, "status_code": None, "error": str(e)[:200]}
