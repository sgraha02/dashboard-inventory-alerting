# Databricks notebook source
# MAGIC %md
# MAGIC # Dashboard Inventory — Create SQL Alerts
# MAGIC Programmatically creates the 4 environment-tier SQL Alerts (Alerts V2).
# MAGIC Idempotent — skips alerts that already exist.

# COMMAND ----------

dbutils.widgets.text("target_table", "uapdev.sandbox_silver.dashboard_inventory_t", "Target Table")
TARGET_TABLE = dbutils.widgets.get("target_table")

# COMMAND ----------

from databricks.sdk import WorkspaceClient
import requests as _req

w = WorkspaceClient()
host = w.config.host.rstrip("/")
headers = {**dict(w.config.authenticate()), "Content-Type": "application/json"}

ALERT_DEFS = [
    {
        "display_name": "Prod_Dashboard_Alert",
        "environment_filter": "Production",
        "subject_prefix": "[PROD]",
        "body_text": (
            "The following dashboards were created in PRODUCTION workspaces "
            "in the last 24 hours.\n\nACTION REQUIRED: Review for compliance "
            "with dashboard guidelines (naming, data sources, permissions)."
        ),
    },
    {
        "display_name": "Cat_Dashboard_Alert",
        "environment_filter": "Test",
        "subject_prefix": "[TEST]",
        "body_text": (
            "The following dashboards were created in TEST / CAT workspaces "
            "in the last 24 hours.\n\nFOR AWARENESS: Test environment activity."
        ),
    },
    {
        "display_name": "Dev_Dashboard_Alert",
        "environment_filter": "Dev",
        "subject_prefix": "[DEV]",
        "body_text": (
            "The following dashboards were created in DEVELOPMENT workspaces "
            "in the last 24 hours.\n\nFOR AWARENESS: Development activity."
        ),
    },
    {
        "display_name": "Sit_Dashboard_Alert",
        "environment_filter": "SIT",
        "subject_prefix": "[SIT-DEPRECATED]",
        "body_text": (
            "The following dashboards were created in SIT workspaces in the "
            "last 24 hours.\n\n\u26a0\ufe0f DEPRECATION NOTICE: This workspace is "
            "slated for DECOMMISSION. New dashboards should NOT be created here."
        ),
    },
]

def _build_condition_query(env_filter: str) -> str:
    return f"""SELECT COUNT(*) AS new_dashboards
FROM {TARGET_TABLE}
WHERE CAST(seed_timestamp AS DATE) = CURRENT_DATE()
  AND workspace_environment = '{env_filter}'
  AND lifecycle_status = 'ACTIVE'"""

# Check existing alerts
existing_alerts = {}
try:
    resp = _req.get(f"{host}/api/2.0/sql/alerts", headers=headers, timeout=30)
    resp.raise_for_status()
    for a in resp.json().get("results", []):
        existing_alerts[a.get("display_name") or a.get("name", "")] = a.get("id")
except Exception as e:
    print(f"Warning: could not list existing alerts: {e}")

created_count = 0
skipped_count = 0

for alert_def in ALERT_DEFS:
    name = alert_def["display_name"]
    env = alert_def["environment_filter"]

    if name in existing_alerts:
        print(f"\u2705 Alert '{name}' already exists (id={existing_alerts[name]}). Skipping.")
        skipped_count += 1
        continue

    payload = {
        "display_name": name,
        "condition": {
            "op": "GREATER_THAN",
            "operand": {"column": {"name": "new_dashboards"}},
            "threshold": {"value": {"double_value": 0}},
        },
        "query_text": _build_condition_query(env),
        "custom_subject": f"{alert_def['subject_prefix']} New Dashboards \u2014 created on {{{{QUERY_RESULT_DATE}}}}",
        "custom_body": alert_def["body_text"],
    }

    try:
        resp = _req.post(
            f"{host}/api/2.0/sql/alerts",
            headers=headers, json=payload, timeout=30,
        )
        resp.raise_for_status()
        alert_id = resp.json().get("id")
        print(f"\u2705 Created alert '{name}' (id={alert_id}) \u2014 environment={env}")
        created_count += 1
    except Exception as e:
        print(f"\u274c Failed to create alert '{name}': {e}")

print(f"\nSummary: {created_count} created, {skipped_count} already existed.")
