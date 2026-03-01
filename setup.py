"""
Interactive setup wizard for the Amazon Ads → BigQuery pipeline.

Handles:
  1. Amazon LWA OAuth flow (opens browser, catches redirect, writes refresh token)
  2. Profile discovery to find your Saudi Arabia profile ID
  3. Writes all values directly into .env

Usage:
    python setup.py
"""
import os
import sys
import json
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import dotenv_values, set_key

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
ENV_EXAMPLE = os.path.join(os.path.dirname(__file__), ".env.example")

REDIRECT_PORT = 9090
REDIRECT_URI_LOCAL = f"http://localhost:{REDIRECT_PORT}/callback"
REDIRECT_URI_SIMPLE = "https://amazon.com"
TOKEN_URL = "https://api.amazon.com/auth/o2/token"
AUTH_URL = "https://eu.account.amazon.com/ap/oa"  # EU/ME region (SA uses EU endpoint)
SCOPE = "advertising::campaign_management"
ADS_EU_BASE = "https://advertising-api-eu.amazon.com"


# ── Helpers ─────────────────────────────────────────────────────────────────

def print_step(n: int, text: str):
    print(f"\n\033[1;36m[Step {n}]\033[0m {text}")

def print_ok(text: str):
    print(f"  \033[1;32m✓\033[0m {text}")

def print_err(text: str):
    print(f"  \033[1;31m✗\033[0m {text}")

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  → {prompt}{suffix}: ").strip()
    return val or default


def ensure_env_file():
    if not os.path.exists(ENV_FILE):
        if os.path.exists(ENV_EXAMPLE):
            import shutil
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
            print_ok(f".env created from .env.example")
        else:
            open(ENV_FILE, "w").close()
            print_ok(".env created (empty)")


def write_env(key: str, value: str):
    set_key(ENV_FILE, key, value)


def read_env() -> dict:
    return dotenv_values(ENV_FILE)


# ── Step 1: Collect Client ID / Secret ──────────────────────────────────────

def step_credentials() -> tuple[str, str]:
    print_step(1, "Amazon Developer App Credentials")
    print("  Find these at: https://advertising.amazon.com/API/docs/en-us/getting-started/create-authorization-grant\n")

    env = read_env()
    client_id = ask("Client ID", env.get("AMAZON_CLIENT_ID", ""))
    client_secret = ask("Client Secret", env.get("AMAZON_CLIENT_SECRET", ""))

    if not client_id or not client_secret:
        print_err("Client ID and Client Secret are required.")
        sys.exit(1)

    write_env("AMAZON_CLIENT_ID", client_id)
    write_env("AMAZON_CLIENT_SECRET", client_secret)
    print_ok("Credentials saved to .env")
    return client_id, client_secret


# ── Step 2: OAuth flow ───────────────────────────────────────────────────────

def step_oauth(client_id: str, client_secret: str) -> str:
    print_step(2, "Amazon LWA OAuth – Getting Refresh Token")

    # Use simple redirect (https://amazon.com) by default — works on headless servers.
    # The user opens the auth URL in any browser; after Allow they land on amazon.com
    # with ?code=... in the address bar. They copy that URL and paste it here.
    print(f"  IMPORTANT: In your Amazon Developer app (LWA Web Settings) make sure")
    print(f"  this URL is in Allowed Return URLs:\n")
    print(f"      {REDIRECT_URI_SIMPLE}\n")
    input("  Press ENTER when you've added the return URL and are ready to continue …")

    redirect_uri = REDIRECT_URI_SIMPLE
    auth_url = (
        f"{AUTH_URL}?client_id={client_id}"
        f"&scope={SCOPE}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
    )

    print(f"\n  Open this URL in any browser (laptop, phone, etc.):")
    print(f"\n  {auth_url}\n")
    print(f"  Sign in with the Amazon account that owns the Ads account.")
    print(f"  Click Allow on the consent screen.")
    print(f"  You will land on amazon.com — copy the FULL URL from your address bar.")
    print(f"  It will look like:  https://www.amazon.com/?code=ANxxxxx&scope=advertising...\n")

    redirect_url = input("  Paste the full redirect URL here: ").strip()
    params = parse_qs(urlparse(redirect_url).query)

    if "error" in params:
        print_err(f"Authorization failed: {params.get('error_description', ['Unknown error'])[0]}")
        sys.exit(1)

    if "code" not in params:
        print_err("No authorization code found. Make sure you copied the full URL from the address bar.")
        sys.exit(1)

    auth_code = params["code"][0]
    print_ok("Authorization code received. Exchanging for refresh token …")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )

    if not resp.ok:
        print_err(f"Token exchange failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        print_err(f"No refresh_token in response: {tokens}")
        sys.exit(1)

    write_env("AMAZON_REFRESH_TOKEN", refresh_token)
    print_ok("Refresh token saved to .env")
    return refresh_token


# ── Step 3: Discover Saudi Arabia Profile ID ─────────────────────────────────

def step_profile(client_id: str, refresh_token: str) -> str:
    print_step(3, "Discovering Saudi Arabia Profile ID")

    # Get a fresh access token
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": read_env().get("AMAZON_CLIENT_SECRET", ""),
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]

    profiles_resp = requests.get(
        f"{ADS_EU_BASE}/v2/profiles",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Amazon-Advertising-API-ClientId": client_id,
        },
        timeout=30,
    )
    profiles_resp.raise_for_status()
    profiles = profiles_resp.json()

    print(f"\n  Found {len(profiles)} profile(s):\n")
    sa_profiles = []
    for p in profiles:
        info = p.get("accountInfo", {})
        country = p.get("countryCode", "??")
        pid = p.get("profileId", "")
        name = info.get("name", "")
        atype = info.get("type", "")
        marker = " ← SA" if country == "SA" else ""
        print(f"    profileId={pid}  countryCode={country}  type={atype}  name={name}{marker}")
        if country == "SA":
            sa_profiles.append(pid)

    print()

    if len(sa_profiles) == 1:
        profile_id = str(sa_profiles[0])
        print_ok(f"Auto-selected SA profile: {profile_id}")
    elif len(sa_profiles) > 1:
        profile_id = ask("Multiple SA profiles found. Enter the profileId to use")
    else:
        print("  No SA profile found automatically.")
        profile_id = ask("Enter your SA profileId manually")

    write_env("AMAZON_PROFILE_ID", profile_id)
    print_ok(f"Profile ID saved to .env")
    return profile_id


# ── Step 4: BigQuery credentials ─────────────────────────────────────────────

def step_bigquery():
    print_step(4, "Google BigQuery Settings")
    env = read_env()

    bq_key = ask("Path to your BQ service account JSON key", env.get("GOOGLE_APPLICATION_CREDENTIALS", ""))
    if not os.path.exists(bq_key):
        print_err(f"File not found: {bq_key}")
        sys.exit(1)

    project_id = ask("GCP Project ID", env.get("BQ_PROJECT_ID", ""))
    dataset_id = ask("BigQuery Dataset name", env.get("BQ_DATASET_ID", "amazon_ads_sa"))

    write_env("GOOGLE_APPLICATION_CREDENTIALS", bq_key)
    write_env("BQ_PROJECT_ID", project_id)
    write_env("BQ_DATASET_ID", dataset_id)
    print_ok("BigQuery settings saved to .env")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n\033[1;33m╔══════════════════════════════════════════════════╗")
    print("║   Amazon Ads → BigQuery Setup Wizard (Saudi Arabia) ║")
    print("╚══════════════════════════════════════════════════╝\033[0m")

    ensure_env_file()

    client_id, client_secret = step_credentials()
    refresh_token = step_oauth(client_id, client_secret)
    step_profile(client_id, refresh_token)
    step_bigquery()

    print("\n\033[1;32m✓ Setup complete! Your .env is ready.\033[0m")
    print("\nRun the pipeline:")
    print("  python main.py               # last 7 days")
    print("  python main.py --date 2024-01-15  # specific date")
    print("  python main.py --schedule    # daily at 06:00 AM\n")


if __name__ == "__main__":
    main()
