"""
Main entrypoint for the Amazon Ads → BigQuery pipeline (Saudi Arabia).

Usage:
    # Run for the past 7 days (default)
    python main.py

    # Run for a specific date
    python main.py --date 2024-01-15

    # Run for a date range
    python main.py --start-date 2024-01-01 --end-date 2024-01-15

    # Discover your Saudi Arabia profile ID
    python main.py --list-profiles

    # Schedule daily runs at 06:00 AM
    python main.py --schedule
"""
import argparse
import sys
import time
from datetime import date, timedelta

import schedule
from loguru import logger

from amazon_ads_client import AmazonAdsClient
from bigquery_loader import BigQueryLoader
from config import Config


def date_range(start: date, end: date):
    """Yield each date from start to end (inclusive)."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def run_pipeline(report_date: str):
    """Fetch Amazon Ads data for Saudi Arabia and load into BigQuery."""
    logger.info(f"═══ Starting pipeline for {report_date} (SA marketplace) ═══")

    try:
        client = AmazonAdsClient()
        loader = BigQueryLoader()

        logger.info("Fetching reports from Amazon Ads API …")
        all_data = client.fetch_all(report_date)

        total_records = sum(len(v) for v in all_data.values())
        logger.info(f"Fetched {total_records} total records across all ad types.")

        logger.info("Loading data into BigQuery …")
        loader.load_all(all_data, report_date)

        logger.success(f"═══ Pipeline completed successfully for {report_date} ═══")

    except Exception as exc:
        logger.error(f"Pipeline failed for {report_date}: {exc}")
        raise


def list_profiles():
    """Print all Amazon Ads profiles to help identify the SA profile ID."""
    client = AmazonAdsClient()
    profiles = client.list_profiles()
    logger.info(f"Found {len(profiles)} profile(s):")
    for p in profiles:
        print(
            f"  profileId={p.get('profileId')}  "
            f"countryCode={p.get('countryCode')}  "
            f"marketplace={p.get('accountInfo', {}).get('marketplaceStringId')}  "
            f"type={p.get('accountInfo', {}).get('type')}  "
            f"name={p.get('accountInfo', {}).get('name')}"
        )


def scheduled_job():
    today = (date.today() - timedelta(days=1)).isoformat()  # yesterday's data
    logger.info(f"Scheduled run triggered for date: {today}")
    run_pipeline(today)


def main():
    parser = argparse.ArgumentParser(
        description="Amazon Ads SA → BigQuery pipeline"
    )
    parser.add_argument("--date", help="Fetch data for a single date (YYYY-MM-DD)")
    parser.add_argument("--start-date", help="Start of date range (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End of date range (YYYY-MM-DD)")
    parser.add_argument("--lookback-days", type=int, default=Config.LOOKBACK_DAYS,
                        help="Number of days back from today (default: 7)")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List all Amazon Ads profiles and exit")
    parser.add_argument("--schedule", action="store_true",
                        help="Run as a daily scheduled job at 06:00 AM")
    args = parser.parse_args()

    # ── Profile discovery mode ──────────────────────────────────────────────
    if args.list_profiles:
        list_profiles()
        sys.exit(0)

    # ── Scheduled mode ──────────────────────────────────────────────────────
    if args.schedule:
        logger.info("Starting scheduler – will run daily at 06:00 AM …")
        schedule.every().day.at("06:00").do(scheduled_job)
        while True:
            schedule.run_pending()
            time.sleep(60)

    # ── One-off run ─────────────────────────────────────────────────────────
    if args.date:
        run_pipeline(args.date)

    elif args.start_date and args.end_date:
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
        for d in date_range(start, end):
            run_pipeline(d.isoformat())

    else:
        # Default: last N days
        today = date.today()
        start = today - timedelta(days=args.lookback_days)
        end = today - timedelta(days=1)
        logger.info(f"No date specified – running for the last {args.lookback_days} days ({start} → {end})")
        for d in date_range(start, end):
            run_pipeline(d.isoformat())


if __name__ == "__main__":
    main()
