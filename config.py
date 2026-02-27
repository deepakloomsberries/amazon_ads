"""
Configuration loader for Amazon Ads → BigQuery pipeline.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Amazon Ads OAuth ────────────────────────────────────────────────────
    CLIENT_ID = os.environ["AMAZON_CLIENT_ID"]
    CLIENT_SECRET = os.environ["AMAZON_CLIENT_SECRET"]
    REFRESH_TOKEN = os.environ["AMAZON_REFRESH_TOKEN"]

    # ── Amazon Ads API ──────────────────────────────────────────────────────
    # Saudi Arabia is served by the EU region endpoint
    ADS_REGION = os.getenv("AMAZON_ADS_REGION", "eu")
    PROFILE_ID = os.environ["AMAZON_PROFILE_ID"]
    MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "A17E79C6D8DWNP")  # SA

    API_BASE_URLS = {
        "na": "https://advertising-api.amazon.com",
        "eu": "https://advertising-api-eu.amazon.com",
        "fe": "https://advertising-api-fe.amazon.com",
    }

    TOKEN_URL = "https://api.amazon.com/auth/o2/token"

    @classmethod
    def api_base_url(cls) -> str:
        return cls.API_BASE_URLS[cls.ADS_REGION]

    # ── BigQuery ────────────────────────────────────────────────────────────
    BQ_CREDENTIALS_PATH = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    BQ_PROJECT_ID = os.environ["BQ_PROJECT_ID"]
    BQ_DATASET_ID = os.getenv("BQ_DATASET_ID", "amazon_ads_sa")

    # ── Report date range (days back from today) ────────────────────────────
    LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
