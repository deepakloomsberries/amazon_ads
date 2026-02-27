"""
Amazon Ads API client – fetches Sponsored Products, Sponsored Brands,
and Sponsored Display reports for the Saudi Arabia marketplace.

Report flow (v3 async):
  1. POST /reporting/reports  → requestId
  2. GET  /reporting/reports/{requestId}  → poll until status == COMPLETED
  3. GET  report download URL  → download gzip JSON
"""
import gzip
import io
import json
import time
from datetime import date, timedelta

import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from auth import AmazonAdsAuth
from config import Config


class AmazonAdsClient:
    POLL_INTERVAL = 10      # seconds between status polls
    POLL_TIMEOUT = 600      # max seconds to wait for a report

    def __init__(self):
        self.auth = AmazonAdsAuth()
        self.base_url = Config.api_base_url()

    # ── Low-level helpers ───────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}{path}",
            headers=self.auth.get_headers(),
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, path: str) -> dict:
        resp = requests.get(
            f"{self.base_url}{path}",
            headers=self.auth.get_headers(),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Profile discovery ───────────────────────────────────────────────────

    def list_profiles(self) -> list[dict]:
        """Return all profiles; useful to find the SA profile ID."""
        resp = requests.get(
            f"{self.base_url}/v2/profiles",
            headers=self.auth.get_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_sa_profile(self) -> dict | None:
        """Return the profile whose marketplace matches Saudi Arabia."""
        for profile in self.list_profiles():
            if profile.get("countryCode") == "SA":
                return profile
        return None

    # ── Reporting v3 (async) ────────────────────────────────────────────────

    def _request_report(self, ad_type: str, report_date: str, metrics: list[str]) -> str:
        """Submit a report request and return the requestId."""
        payload = {
            "name": f"{ad_type} report {report_date}",
            "startDate": report_date,
            "endDate": report_date,
            "configuration": {
                "adProduct": ad_type,           # SPONSORED_PRODUCTS | SPONSORED_BRANDS | SPONSORED_DISPLAY
                "groupBy": ["campaign", "adGroup"],
                "columns": metrics,
                "reportTypeId": "spCampaigns",
                "timeUnit": "DAILY",
                "format": "GZIP_JSON",
            },
        }
        logger.info(f"Requesting {ad_type} report for {report_date} …")
        result = self._post("/reporting/reports", payload)
        request_id = result["reportId"]
        logger.info(f"Report request submitted. requestId={request_id}")
        return request_id

    def _poll_report(self, request_id: str) -> str:
        """Poll until the report is COMPLETED; return the download URL."""
        deadline = time.time() + self.POLL_TIMEOUT
        while time.time() < deadline:
            status_data = self._get(f"/reporting/reports/{request_id}")
            status = status_data.get("status")
            logger.debug(f"Report {request_id} status: {status}")
            if status == "COMPLETED":
                return status_data["url"]
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Report {request_id} ended with status {status}")
            time.sleep(self.POLL_INTERVAL)
        raise TimeoutError(f"Report {request_id} did not complete within {self.POLL_TIMEOUT}s")

    def _download_report(self, url: str) -> list[dict]:
        """Download and decompress a GZIP_JSON report; return list of records."""
        logger.info("Downloading report …")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        with gzip.open(io.BytesIO(resp.content), "rt", encoding="utf-8") as f:
            data = json.load(f)
        logger.success(f"Downloaded {len(data)} records.")
        return data

    def fetch_report(self, ad_type: str, report_date: str, metrics: list[str]) -> list[dict]:
        """End-to-end: request → poll → download. Returns list of row dicts."""
        request_id = self._request_report(ad_type, report_date, metrics)
        download_url = self._poll_report(request_id)
        records = self._download_report(download_url)
        # Tag every row with metadata
        for row in records:
            row["report_date"] = report_date
            row["ad_type"] = ad_type
            row["marketplace"] = "SA"
            row["profile_id"] = Config.PROFILE_ID
        return records

    # ── Convenience methods for each ad type ───────────────────────────────

    SP_METRICS = [
        "campaignId", "campaignName", "campaignStatus", "campaignBudget",
        "adGroupId", "adGroupName",
        "impressions", "clicks", "spend", "sales7d", "orders7d",
        "unitsSoldClicks7d", "acos7d", "roas7d",
        "date",
    ]

    SB_METRICS = [
        "campaignId", "campaignName", "campaignStatus",
        "adGroupId", "adGroupName",
        "impressions", "clicks", "spend", "sales14d", "orders14d",
        "unitsSoldClicks14d", "acos14d", "roas14d",
        "date",
    ]

    SD_METRICS = [
        "campaignId", "campaignName", "campaignStatus",
        "adGroupId", "adGroupName",
        "impressions", "clicks", "spend", "sales14d", "orders14d",
        "date",
    ]

    def fetch_sponsored_products(self, report_date: str) -> list[dict]:
        return self.fetch_report("SPONSORED_PRODUCTS", report_date, self.SP_METRICS)

    def fetch_sponsored_brands(self, report_date: str) -> list[dict]:
        return self.fetch_report("SPONSORED_BRANDS", report_date, self.SB_METRICS)

    def fetch_sponsored_display(self, report_date: str) -> list[dict]:
        return self.fetch_report("SPONSORED_DISPLAY", report_date, self.SD_METRICS)

    def fetch_all(self, report_date: str) -> dict[str, list[dict]]:
        """Fetch all three ad-type reports for a given date."""
        return {
            "sponsored_products": self.fetch_sponsored_products(report_date),
            "sponsored_brands": self.fetch_sponsored_brands(report_date),
            "sponsored_display": self.fetch_sponsored_display(report_date),
        }
