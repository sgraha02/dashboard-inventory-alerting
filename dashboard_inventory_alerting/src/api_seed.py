# Databricks notebook source
# MAGIC %md
# MAGIC # Dashboard Inventory — API Seed
# MAGIC Populates the inventory table with dashboard metadata from the Lakeview API.
# MAGIC 
# MAGIC **Modes:**
# MAGIC - If `secret_scope` is set: cross-workspace seed using Service Principal
# MAGIC - If `secret_scope` is empty: single-workspace seed using session token (MVP)

# COMMAND ----------

dbutils.widgets.text("target_table", "uapdev.sandbox_silver.dashboard_inventory_t", "Target Table")
dbutils.widgets.text("secret_scope", "", "Secret Scope (empty = MVP mode)")

TARGET_TABLE = dbutils.widgets.get("target_table")
SECRET_SCOPE = dbutils.widgets.get("secret_scope").strip()

print(f"Target table:  {TARGET_TABLE}")
print(f"Secret scope:  {SECRET_SCOPE or '(not set — MVP single-workspace mode)'}")

# COMMAND ----------

import requests
import time
from collections import Counter
from datetime import datetime, timezone
from pyspark.sql import Row
from pyspark.sql.types import (
    StructType, StructField, StringType, LongType,
    TimestampType, DateType, BooleanType,
)
from databricks.sdk import WorkspaceClient

inventory_schema = StructType([
    StructField("dashboard_id",          StringType(),    False),
    StructField("creator_user_name",     StringType(),    True),
    StructField("create_time",           TimestampType(), True),
    StructField("update_time",           TimestampType(), True),
    StructField("parent_path",           StringType(),    True),
    StructField("lifecycle_status",      StringType(),    True),
    StructField("workspace_name",        StringType(),    True),
    StructField("workspace_id",          LongType(),      True),
    StructField("workspace_environment", StringType(),    True),
    StructField("is_deprecated",         BooleanType(),   True),
    StructField("first_seen_date",       DateType(),      True),
    StructField("source",                StringType(),    True),
    StructField("seed_timestamp",        TimestampType(), True),
])

def _parse_ts(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None

def classify_environment(name: str) -> tuple:
    """Return (environment_tier, is_deprecated) based on workspace name."""
    n = name.lower()
    if "-prod-" in n or n.endswith("-prod") or "prod" in n.split("-"):
        return ("Production", False)
    if "-cat-" in n or n.endswith("-cat") or "-test-" in n or n.endswith("-test"):
        return ("Test", False)
    if "-sit-" in n or n.endswith("-sit") or "devsit" in n:
        return ("SIT", True)
    if "-dev-" in n or n.endswith("-dev") or n.startswith("dev-"):
        return ("Dev", False)
    return ("Unclassified", False)

def fetch_dashboards_for_workspace(host, headers, ws_name, ws_id, ws_env, ws_dep, run_ts):
    """Paginate the Lakeview dashboards API for a single workspace."""
    url = f"{host.rstrip('/')}/api/2.0/lakeview/dashboards"
    params = {"page_size": 100}
    rows = []
    page = 0
    while True:
        page += 1
        for attempt in range(5):
            resp = requests.get(url, headers=headers, params=params, timeout=60)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  Rate limited (429) on page {page}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            break
        if resp.status_code in (401, 403):
            raise PermissionError(f"{resp.status_code} — no access to {host}")
        resp.raise_for_status()
        data = resp.json()
        dashboards = data.get("dashboards", [])
        if not dashboards:
            break
        for d in dashboards:
            creator = d.get("creator")
            rows.append(Row(
                dashboard_id=d.get("dashboard_id"),
                creator_user_name=creator.get("user_name") if isinstance(creator, dict) else creator,
                create_time=_parse_ts(d.get("create_time")),
                update_time=_parse_ts(d.get("update_time")),
                parent_path=d.get("parent_path"),
                lifecycle_status=d.get("lifecycle_status", "ACTIVE"),
                workspace_name=ws_name,
                workspace_id=ws_id,
                workspace_environment=ws_env,
                is_deprecated=ws_dep,
                first_seen_date=run_ts.date(),
                source="API_SEED",
                seed_timestamp=run_ts,
            ))
        next_token = data.get("next_page_token")
        if not next_token:
            break
        params = {"page_size": 100, "page_token": next_token}
    print(f"  [{ws_name}] {len(rows)} dashboards ({page} pages)")
    return rows

# COMMAND ----------

run_ts = datetime.now(timezone.utc)
all_rows = []
errors = {}

if SECRET_SCOPE:
    # ---- Full mode: cross-workspace using Service Principal ----
    _client_id     = dbutils.secrets.get(scope=SECRET_SCOPE, key="sp-client-id")
    _client_secret = dbutils.secrets.get(scope=SECRET_SCOPE, key="sp-client-secret")
    _tenant_id     = dbutils.secrets.get(scope=SECRET_SCOPE, key="sp-tenant-id")

    ws_rows = spark.sql("""
        SELECT workspace_id, workspace_name, workspace_url
        FROM system.access.workspaces_latest WHERE status = 'RUNNING'
    """).collect()

    WORKSPACES = {}
    for row in ws_rows:
        env, dep = classify_environment(row.workspace_name)
        host = row.workspace_url
        if host and not host.startswith("https://"):
            host = f"https://{host}"
        WORKSPACES[row.workspace_name] = {
            "host": host, "environment": env, "deprecated": dep,
            "workspace_id": int(row.workspace_id) if row.workspace_id else None,
        }

    print(f"Scanning {len(WORKSPACES)} workspace(s) as SP...")
    for ws_name, ws_meta in WORKSPACES.items():
        try:
            ws_client = WorkspaceClient(
                host=ws_meta["host"],
                azure_client_id=_client_id,
                azure_client_secret=_client_secret,
                azure_tenant_id=_tenant_id,
            )
            headers = dict(ws_client.config.authenticate())
            all_rows.extend(fetch_dashboards_for_workspace(
                ws_meta["host"], headers, ws_name,
                ws_meta["workspace_id"], ws_meta["environment"],
                ws_meta["deprecated"], run_ts,
            ))
        except Exception as e:
            errors[ws_name] = str(e)
            print(f"  [{ws_name}] ERROR: {e}")
else:
    # ---- MVP mode: current workspace only using session token ----
    w = WorkspaceClient()
    host = w.config.host
    headers = dict(w.config.authenticate())

    ws_meta_row = spark.sql(f"""
        SELECT workspace_id, workspace_name
        FROM system.access.workspaces_latest
        WHERE workspace_url = '{host}' OR workspace_url = '{host}/'
        LIMIT 1
    """).collect()

    if ws_meta_row:
        ws_name = ws_meta_row[0].workspace_name
        ws_id = int(ws_meta_row[0].workspace_id)
    else:
        ws_name = host.split("//")[1].split(".")[0] if host else "unknown"
        ws_id = None

    ws_env, ws_dep = classify_environment(ws_name)
    print(f"MVP mode: {ws_name} (id={ws_id}), env={ws_env}")
    all_rows = fetch_dashboards_for_workspace(
        host, headers, ws_name, ws_id, ws_env, ws_dep, run_ts,
    )

print(f"\nTotal: {len(all_rows)} dashboards")
if errors:
    print(f"Errors ({len(errors)}): {list(errors.keys())}")

# COMMAND ----------

# MERGE into inventory table (idempotent)
if all_rows:
    df = spark.createDataFrame(all_rows, schema=inventory_schema)
    df.createOrReplaceTempView("_api_seed_staging")
    spark.sql(f"""
        MERGE INTO {TARGET_TABLE} AS tgt
        USING _api_seed_staging AS src
        ON tgt.dashboard_id = src.dashboard_id
        WHEN MATCHED THEN UPDATE SET
            tgt.update_time      = src.update_time,
            tgt.parent_path      = src.parent_path,
            tgt.lifecycle_status = src.lifecycle_status
        WHEN NOT MATCHED THEN INSERT *
    """)
    count = spark.sql(f"SELECT COUNT(*) AS cnt FROM {TARGET_TABLE}").collect()[0]["cnt"]
    print(f"\u2705 MERGE complete. {TARGET_TABLE} now has {count} rows.")
else:
    print("No dashboards collected.")
