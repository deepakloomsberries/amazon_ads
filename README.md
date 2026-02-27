# Amazon Ads → BigQuery Pipeline (Saudi Arabia)

Fetches **Sponsored Products**, **Sponsored Brands**, and **Sponsored Display** reports from the Amazon Ads API for the **Saudi Arabia (SA)** marketplace and loads them into Google BigQuery.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `AMAZON_CLIENT_ID` | Amazon Developer app Client ID |
| `AMAZON_CLIENT_SECRET` | Amazon Developer app Client Secret |
| `AMAZON_REFRESH_TOKEN` | LWA Refresh Token (see below) |
| `AMAZON_PROFILE_ID` | SA marketplace profile ID (see below) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to your BQ service account JSON key |
| `BQ_PROJECT_ID` | GCP project ID |
| `BQ_DATASET_ID` | BigQuery dataset name (default: `amazon_ads_sa`) |

### 3. Get your Amazon Ads Refresh Token

Use the [Amazon Ads Authorization guide](https://advertising.amazon.com/API/docs/en-us/getting-started/retrieve-access-token) to complete the OAuth flow and obtain a refresh token. Saudi Arabia uses the **EU** region endpoint.

### 4. Find your Saudi Arabia Profile ID

```bash
python main.py --list-profiles
```

Look for the entry with `countryCode=SA` and copy its `profileId` into `AMAZON_PROFILE_ID` in your `.env`.

---

## Usage

```bash
# Fetch last 7 days (default)
python main.py

# Fetch a specific date
python main.py --date 2024-01-15

# Fetch a date range
python main.py --start-date 2024-01-01 --end-date 2024-01-31

# Run as a daily scheduler (runs at 06:00 AM every day)
python main.py --schedule
```

---

## BigQuery Tables

The pipeline creates a dataset (`amazon_ads_sa` by default) with three partitioned tables:

| Table | Ad Type | Key Metrics |
|---|---|---|
| `sponsored_products` | Sponsored Products | impressions, clicks, spend, sales7d, acos7d, roas7d |
| `sponsored_brands` | Sponsored Brands | impressions, clicks, spend, sales14d, acos14d, roas14d |
| `sponsored_display` | Sponsored Display | impressions, clicks, spend, sales14d |

All tables are **partitioned by `report_date`** for cost-efficient querying.
Loads are **idempotent** – re-running for the same date deletes and re-inserts that partition.

---

## Architecture

```
Amazon Ads API (EU endpoint)
        │
        │  OAuth2 (LWA refresh token)
        ▼
 AmazonAdsClient
  ├── Request report (async v3)
  ├── Poll until COMPLETED
  └── Download GZIP JSON
        │
        ▼
 BigQueryLoader
  ├── Auto-create dataset & tables
  ├── Delete existing partition (idempotent)
  └── Load DataFrame via BQ Storage Write API
        │
        ▼
  BigQuery Dataset: amazon_ads_sa
  ├── sponsored_products  (partitioned by report_date)
  ├── sponsored_brands    (partitioned by report_date)
  └── sponsored_display   (partitioned by report_date)
```
