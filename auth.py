"""
Amazon Ads OAuth2 authentication using refresh token (LWA – Login with Amazon).
"""
import time
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config


class AmazonAdsAuth:
    """Manages access tokens via the LWA refresh-token grant."""

    def __init__(self):
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch_token(self) -> dict:
        logger.info("Fetching new Amazon Ads access token …")
        resp = requests.post(
            Config.TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": Config.CLIENT_ID,
                "client_secret": Config.CLIENT_SECRET,
                "refresh_token": Config.REFRESH_TOKEN,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing if expired."""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        data = self._fetch_token()
        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600)
        logger.success("Access token refreshed successfully.")
        return self._access_token

    def get_headers(self) -> dict:
        """Return the standard Amazon Ads API request headers."""
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Amazon-Advertising-API-ClientId": Config.CLIENT_ID,
            "Amazon-Advertising-API-Scope": Config.PROFILE_ID,
            "Content-Type": "application/json",
        }

    def get_headers_no_scope(self) -> dict:
        """Return headers without Amazon-Advertising-API-Scope.

        The /v2/profiles endpoint must NOT receive a scope header – it is the
        bootstrap call used to discover which profile IDs are available.
        """
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Amazon-Advertising-API-ClientId": Config.CLIENT_ID,
            "Content-Type": "application/json",
        }
