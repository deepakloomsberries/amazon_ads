"""
BigQuery loader – creates dataset/tables if needed and upserts Amazon Ads data.
"""
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from loguru import logger

from config import Config


# ── Table schemas ───────────────────────────────────────────────────────────

SPONSORED_PRODUCTS_SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),
    bigquery.SchemaField("ad_type", "STRING"),
    bigquery.SchemaField("marketplace", "STRING"),
    bigquery.SchemaField("profile_id", "STRING"),
    bigquery.SchemaField("campaignId", "STRING"),
    bigquery.SchemaField("campaignName", "STRING"),
    bigquery.SchemaField("campaignStatus", "STRING"),
    bigquery.SchemaField("campaignBudget", "FLOAT64"),
    bigquery.SchemaField("adGroupId", "STRING"),
    bigquery.SchemaField("adGroupName", "STRING"),
    bigquery.SchemaField("impressions", "INT64"),
    bigquery.SchemaField("clicks", "INT64"),
    bigquery.SchemaField("spend", "FLOAT64"),
    bigquery.SchemaField("sales7d", "FLOAT64"),
    bigquery.SchemaField("orders7d", "INT64"),
    bigquery.SchemaField("unitsSoldClicks7d", "INT64"),
    bigquery.SchemaField("acos7d", "FLOAT64"),
    bigquery.SchemaField("roas7d", "FLOAT64"),
    bigquery.SchemaField("date", "DATE"),
]

SPONSORED_BRANDS_SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),
    bigquery.SchemaField("ad_type", "STRING"),
    bigquery.SchemaField("marketplace", "STRING"),
    bigquery.SchemaField("profile_id", "STRING"),
    bigquery.SchemaField("campaignId", "STRING"),
    bigquery.SchemaField("campaignName", "STRING"),
    bigquery.SchemaField("campaignStatus", "STRING"),
    bigquery.SchemaField("adGroupId", "STRING"),
    bigquery.SchemaField("adGroupName", "STRING"),
    bigquery.SchemaField("impressions", "INT64"),
    bigquery.SchemaField("clicks", "INT64"),
    bigquery.SchemaField("spend", "FLOAT64"),
    bigquery.SchemaField("sales14d", "FLOAT64"),
    bigquery.SchemaField("orders14d", "INT64"),
    bigquery.SchemaField("unitsSoldClicks14d", "INT64"),
    bigquery.SchemaField("acos14d", "FLOAT64"),
    bigquery.SchemaField("roas14d", "FLOAT64"),
    bigquery.SchemaField("date", "DATE"),
]

SPONSORED_DISPLAY_SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),
    bigquery.SchemaField("ad_type", "STRING"),
    bigquery.SchemaField("marketplace", "STRING"),
    bigquery.SchemaField("profile_id", "STRING"),
    bigquery.SchemaField("campaignId", "STRING"),
    bigquery.SchemaField("campaignName", "STRING"),
    bigquery.SchemaField("campaignStatus", "STRING"),
    bigquery.SchemaField("adGroupId", "STRING"),
    bigquery.SchemaField("adGroupName", "STRING"),
    bigquery.SchemaField("impressions", "INT64"),
    bigquery.SchemaField("clicks", "INT64"),
    bigquery.SchemaField("spend", "FLOAT64"),
    bigquery.SchemaField("sales14d", "FLOAT64"),
    bigquery.SchemaField("orders14d", "INT64"),
    bigquery.SchemaField("date", "DATE"),
]

TABLE_CONFIGS = {
    "sponsored_products": {
        "table_id": "sponsored_products",
        "schema": SPONSORED_PRODUCTS_SCHEMA,
        "partition_field": "report_date",
    },
    "sponsored_brands": {
        "table_id": "sponsored_brands",
        "schema": SPONSORED_BRANDS_SCHEMA,
        "partition_field": "report_date",
    },
    "sponsored_display": {
        "table_id": "sponsored_display",
        "schema": SPONSORED_DISPLAY_SCHEMA,
        "partition_field": "report_date",
    },
}


class BigQueryLoader:
    def __init__(self):
        credentials = service_account.Credentials.from_service_account_file(
            Config.BQ_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self.client = bigquery.Client(
            project=Config.BQ_PROJECT_ID,
            credentials=credentials,
        )
        self.dataset_id = Config.BQ_DATASET_ID
        self._ensure_dataset()

    # ── Dataset / table setup ───────────────────────────────────────────────

    def _ensure_dataset(self):
        dataset_ref = f"{Config.BQ_PROJECT_ID}.{self.dataset_id}"
        try:
            self.client.get_dataset(dataset_ref)
            logger.info(f"Dataset '{self.dataset_id}' already exists.")
        except Exception:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = "EU"   # closest region to Saudi Arabia
            self.client.create_dataset(dataset, exists_ok=True)
            logger.success(f"Dataset '{self.dataset_id}' created in EU region.")

    def _ensure_table(self, table_name: str, schema: list, partition_field: str):
        table_ref = f"{Config.BQ_PROJECT_ID}.{self.dataset_id}.{table_name}"
        try:
            self.client.get_table(table_ref)
            logger.info(f"Table '{table_name}' already exists.")
        except Exception:
            table = bigquery.Table(table_ref, schema=schema)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=partition_field,
            )
            self.client.create_table(table, exists_ok=True)
            logger.success(f"Table '{table_name}' created with partitioning on '{partition_field}'.")

    # ── Load data ───────────────────────────────────────────────────────────

    def _delete_partition(self, table_name: str, report_date: str):
        """Delete existing rows for the given date to allow idempotent loads."""
        query = f"""
            DELETE FROM `{Config.BQ_PROJECT_ID}.{self.dataset_id}.{table_name}`
            WHERE report_date = '{report_date}'
        """
        logger.info(f"Deleting existing rows for {report_date} in '{table_name}' …")
        self.client.query(query).result()

    def load(self, ad_type_key: str, records: list[dict], report_date: str):
        """
        Load records for a given ad type into BigQuery.
        Deletes the partition first for idempotency.
        """
        if not records:
            logger.warning(f"No records to load for {ad_type_key} on {report_date}.")
            return

        cfg = TABLE_CONFIGS[ad_type_key]
        table_name = cfg["table_id"]
        schema = cfg["schema"]
        partition_field = cfg["partition_field"]

        self._ensure_table(table_name, schema, partition_field)
        self._delete_partition(table_name, report_date)

        df = pd.DataFrame(records)

        # Cast date columns
        for col in ["report_date", "date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col]).dt.date

        # Coerce numeric columns to correct types
        schema_field_types = {f.name: f.field_type for f in schema}
        for col in df.columns:
            bq_type = schema_field_types.get(col)
            if bq_type == "INT64":
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
            elif bq_type == "FLOAT64":
                df[col] = pd.to_numeric(df[col], errors="coerce")

        table_ref = f"{Config.BQ_PROJECT_ID}.{self.dataset_id}.{table_name}"
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )

        job = self.client.load_table_from_dataframe(df, table_ref, job_config=job_config)
        job.result()
        logger.success(f"Loaded {len(df)} rows into '{table_name}' for {report_date}.")

    def load_all(self, all_data: dict[str, list[dict]], report_date: str):
        """Load all ad-type reports into their respective BQ tables."""
        for ad_type_key, records in all_data.items():
            self.load(ad_type_key, records, report_date)
