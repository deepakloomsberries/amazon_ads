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
    POLL_INTERVAL_INITIAL = 10   # seconds for first poll interval
    POLL_INTERVAL_MAX = 60       # cap for exponential back-off
    POLL_TIMEOUT = 1800          # max seconds to wait for a report (30 min)

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
        if not resp.ok:
            logger.error(f"POST {path} returned {resp.status_code}: {resp.text!r}")
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
        """Return all profiles; useful to find the SA profile ID.

        Uses headers without Amazon-Advertising-API-Scope because /v2/profiles
        is the bootstrap endpoint – no profile ID is required (or allowed).
        """
        resp = requests.get(
            f"{self.base_url}/v2/profiles",
            headers=self.auth.get_headers_no_scope(),
            timeout=30,
        )
        if not resp.ok:
            logger.error(
                f"GET /v2/profiles returned {resp.status_code}: {resp.text!r}"
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

    # Maps adProduct → the correct reportTypeId for the campaign-level report
    REPORT_TYPE_IDS = {
        "SPONSORED_PRODUCTS": "spCampaigns",
        "SPONSORED_BRANDS":   "sbCampaigns",
        "SPONSORED_DISPLAY":  "sdCampaigns",
    }

    # sbCampaigns / sdCampaigns only support groupBy=["campaign"];
    # only spCampaigns supports adGroup-level grouping.
    GROUP_BY = {
        "SPONSORED_PRODUCTS": ["campaign", "adGroup"],
        "SPONSORED_BRANDS":   ["campaign"],
        "SPONSORED_DISPLAY":  ["campaign"],
    }

    def _request_report(self, ad_type: str, report_date: str, metrics: list[str]) -> str:
        """Submit a report request and return the requestId."""
        report_type_id = self.REPORT_TYPE_IDS.get(ad_type, "spCampaigns")
        group_by = self.GROUP_BY.get(ad_type, ["campaign"])
        payload = {
            "name": f"{ad_type} report {report_date}",
            "startDate": report_date,
            "endDate": report_date,
            "configuration": {
                "adProduct": ad_type,           # SPONSORED_PRODUCTS | SPONSORED_BRANDS | SPONSORED_DISPLAY
                "groupBy": group_by,
                "columns": metrics,
                "reportTypeId": report_type_id,
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
        """Poll until the report is COMPLETED; return the download URL.

        Uses exponential back-off for the sleep interval (10 s → 60 s cap) to
        avoid hammering the API during the longer waits that Amazon Ads reports
        sometimes require.
        """
        start = time.time()
        deadline = start + self.POLL_TIMEOUT
        interval = self.POLL_INTERVAL_INITIAL
        while time.time() < deadline:
            status_data = self._get(f"/reporting/reports/{request_id}")
            status = status_data.get("status")
            elapsed = int(time.time() - start)
            logger.debug(f"Report {request_id} status: {status} (elapsed {elapsed}s)")
            if status == "COMPLETED":
                return status_data["url"]
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Report {request_id} ended with status {status}")
            # Sleep for the current interval, but don't overshoot the deadline.
            sleep_for = min(interval, deadline - time.time())
            if sleep_for > 0:
                time.sleep(sleep_for)
            interval = min(interval * 2, self.POLL_INTERVAL_MAX)
        raise TimeoutError(
            f"Report {request_id} did not complete within {self.POLL_TIMEOUT}s"
        )

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
        # Tag every row with metadata and normalise API column names → BQ schema names
        for row in records:
            row["report_date"] = report_date
            row["ad_type"] = ad_type
            row["marketplace"] = "SA"
            row["profile_id"] = Config.PROFILE_ID

            # Rename: API name → BQ schema name
            if "cost" in row:
                row["spend"] = row.pop("cost")
            if "campaignBudgetAmount" in row:
                row["campaignBudget"] = row.pop("campaignBudgetAmount")
            # SP: attributed metrics carry a window suffix
            if "purchases7d" in row:
                row["orders7d"] = row.pop("purchases7d")
            # SB/SD: campaign-level metrics use plain names (no window suffix)
            if "sales" in row:
                row["sales14d"] = row.pop("sales")
            if "purchases" in row:
                row["orders14d"] = row.pop("purchases")
            if "unitsSoldClicks" in row:
                row["unitsSoldClicks14d"] = row.pop("unitsSoldClicks")

            # Compute derived metrics (ACOS / ROAS) – scoped per ad type
            spend = row.get("spend") or 0
            if ad_type == "SPONSORED_PRODUCTS":
                sales7d = row.get("sales7d") or 0
                row["acos7d"] = round(spend / sales7d, 4) if sales7d else None
                row["roas7d"] = round(sales7d / spend, 4) if spend else None
            elif ad_type == "SPONSORED_BRANDS":
                sales14d = row.get("sales14d") or 0
                row["acos14d"] = round(spend / sales14d, 4) if sales14d else None
                row["roas14d"] = round(sales14d / spend, 4) if spend else None

        return records

    # ── Convenience methods for each ad type ───────────────────────────────

    # Columns requested from the Amazon Ads API v3 (use API names, not BQ schema names).
    # cost → spend, campaignBudgetAmount → campaignBudget, purchases* → orders*  (renamed in fetch_report).
    # "date" is the correct column for timeUnit=DAILY (not startDate/endDate).
    # acos/roas are derived; not returned by the API directly.
    SP_METRICS = [
        "campaignId", "campaignName", "campaignStatus", "campaignBudgetAmount",
        "adGroupId", "adGroupName",
        "impressions", "clicks", "cost", "sales7d", "purchases7d",
        "unitsSoldClicks7d", "date",
    ]

    # sbCampaigns: campaign-level only; metrics have no attribution-window suffix.
    # sales→sales14d, purchases→orders14d, unitsSoldClicks→unitsSoldClicks14d (renamed in fetch_report).
    SB_METRICS = [
        "campaignId", "campaignName", "campaignStatus", "campaignBudgetAmount",
        "impressions", "clicks", "cost", "sales", "purchases",
        "unitsSoldClicks", "date",
    ]

    # sdCampaigns: same campaign-level restriction as SB.
    SD_METRICS = [
        "campaignId", "campaignName", "campaignStatus", "campaignBudgetAmount",
        "impressions", "clicks", "cost", "sales", "purchases",
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
